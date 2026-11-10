"use strict";

/**
 * Compares RSEFARINA CPPT data from the HIS web UI (scraped via Puppeteer)
 * against the RSEFARINA API (`POST /api/serenic`, resource "cppt") for the
 * same noRegistrasi.
 *
 * Usage:
 *   node compare.js [detailUrl] [startDate] [endDate]
 *
 * Defaults to the REG/IP/261013-0044 detail page used during exploration,
 * with startDate derived from the registration number and endDate = today.
 */

const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer");

const CRON_API_SCHEDULER_DIR = path.resolve(
  __dirname,
  "../../cron-api-scheduler",
);

// Pre-load cron-api-scheduler's .env before requiring its client, so
// RSEFARINA_API_BASE_URL / RSEFARINA_API_KEY resolve regardless of cwd.
require("dotenv").config({
  path: path.join(CRON_API_SCHEDULER_DIR, ".env"),
});
const { fetchCppt } = require(
  path.join(
    CRON_API_SCHEDULER_DIR,
    "src/modules/hospitals/rsefarina/client.js",
  ),
);

const HIS_BASE_URL = "http://36.64.110.114:8083";
const HIS_USERNAME = "serenic";
const HIS_PASSWORD = "3krlinc1P3nan3";

const OUTPUT_DIR = __dirname;

const DEFAULT_DETAIL_URL =
  HIS_BASE_URL +
  "/Live2/Module/RADT/EmrIp/EmrIpDetail.aspx?rt=IPR&regno=REG/IP/261013-0044&fregno=REG/EM/261013-0043&parid=MD-00268&unit=D22&room=2203&patid=P20261013-0029";

const NBSP = "\u00A0";

const normalize = (value) =>
  (value || "").split(NBSP).join(" ").trim();

const normalizeText = (value) =>
  (value || "")
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .trim();

const extractNoRegistrasi = (detailUrl) => {
  const match = detailUrl.match(/regno=([^&]+)/);
  if (!match) throw new Error("Tidak menemukan query param 'regno' di URL");
  return decodeURIComponent(match[1]);
};

const deriveStartDate = (noRegistrasi) => {
  // REG/IP/261013-0044 -> 26-10-13 -> 2026-10-13
  const match = noRegistrasi.match(/(\d{2})(\d{2})(\d{2})-\d+$/);
  if (!match) return null;
  const [, yy, mm, dd] = match;
  return `20${yy}-${mm}-${dd}`;
};

const todayIso = () => new Date().toISOString().slice(0, 10);

// --- Web scraping -----------------------------------------------------

const scrapeWeb = async (detailUrl) => {
  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1366, height: 900 });

    await page.goto(detailUrl, { waitUntil: "networkidle2", timeout: 30000 });

    if (page.url().includes("Login.aspx")) {
      await page.waitForSelector("#ctl00_ContentPlaceHolder1_txtUserID", {
        timeout: 15000,
      });
      await page.type("#ctl00_ContentPlaceHolder1_txtUserID", HIS_USERNAME, {
        delay: 20,
      });
      await page.type("#ctl00_ContentPlaceHolder1_txtPassword", HIS_PASSWORD, {
        delay: 20,
      });

      await Promise.all([
        page.waitForNavigation({ waitUntil: "networkidle2", timeout: 30000 }),
        page.click("#ctl00_ContentPlaceHolder1_btnLogin_input"),
      ]);

      if (!page.url().includes("EmrIpDetail.aspx")) {
        await page.goto(detailUrl, {
          waitUntil: "networkidle2",
          timeout: 30000,
        });
      }
    }

    return await page.evaluate(() => document.body.innerText);
  } finally {
    await browser.close();
  }
};

// Parses the grdAssessment grid's linearized innerText into structured
// entries. Each grid row renders as one tab-separated line:
//   <empty> <date> <performer1> <performer2> <role> <regno> <unit> <title> <category> <trailing>
// followed by freeform detail lines (e.g. "S:\t...", "O:\t...") until the
// next header line.
const HEADER_LINE_RE = /^\d{2}\/\d{2}\/\d{4} \d{2}:\d{2}$/;

const parseWebEntries = (bodyText) => {
  const lines = bodyText.split("\n");
  const entries = [];
  let current = null;

  const flush = () => {
    if (!current) return;
    current.rawBody = current.bodyLines.join("\n").trim();
    if (current.category === "SOAP") {
      current.soap = parseSoapBlock(current.bodyLines);
    }
    delete current.bodyLines;
    entries.push(current);
    current = null;
  };

  for (const line of lines) {
    const parts = line.split("\t");
    const isHeader =
      parts.length >= 9 &&
      parts[0].trim() === "" &&
      HEADER_LINE_RE.test(parts[1].trim());

    if (isHeader) {
      flush();
      current = {
        dateLocal: parts[1].trim(),
        performer1: normalize(parts[2]),
        performer2: normalize(parts[3]),
        role: normalize(parts[4]),
        regno: normalize(parts[5]),
        unit: normalize(parts[6]),
        title: normalize(parts[7]),
        category: normalize(parts[8]),
        bodyLines: [],
      };
      continue;
    }

    if (current) current.bodyLines.push(line);
  }
  flush();

  for (const entry of entries) {
    entry.dateUtcMinute = localWibToUtcMinuteIso(entry.dateLocal);
  }

  return entries;
};

// Detail body for a SOAP row has lines prefixed "S:", "O:", "A:", "P:"; each
// field's text continues on following lines until the next marker.
const parseSoapBlock = (bodyLines) => {
  const fieldByMarker = { S: "subjective", O: "objective", A: "assessment", P: "plan" };
  const result = { subjective: "", objective: "", assessment: "", plan: "" };
  let activeField = null;

  for (const rawLine of bodyLines) {
    const markerMatch = rawLine.match(/^(S|O|A|P):\t?(.*)$/);
    if (markerMatch) {
      activeField = fieldByMarker[markerMatch[1]];
      result[activeField] += (result[activeField] ? "\n" : "") + markerMatch[2];
      continue;
    }
    if (activeField && rawLine.trim() !== "" && rawLine.trim() !== NBSP) {
      result[activeField] += "\n" + rawLine;
    }
  }

  for (const key of Object.keys(result)) {
    result[key] = normalizeText(result[key]);
  }
  return result;
};

// "13/10/2026 16:34" (WIB, UTC+7) -> "2026-10-13T09:34" (UTC, minute precision)
const localWibToUtcMinuteIso = (dateLocal) => {
  const match = dateLocal.match(
    /^(\d{2})\/(\d{2})\/(\d{4}) (\d{2}):(\d{2})$/,
  );
  if (!match) return null;
  const [, dd, mm, yyyy, hh, min] = match;
  const asUtcMs = Date.UTC(
    Number(yyyy),
    Number(mm) - 1,
    Number(dd),
    Number(hh) - 7,
    Number(min),
  );
  return new Date(asUtcMs).toISOString().slice(0, 16);
};

// --- API fetching -------------------------------------------------------

const fetchApiSoap = async (noRegistrasi, startDate, endDate) => {
  const raw = await fetchCppt({ noRegistrasi, startDate, endDate });
  const entry = Array.isArray(raw.cppt) ? raw.cppt[0] : null;
  const soap = entry && Array.isArray(entry.soap) ? entry.soap : [];

  return {
    raw,
    soap: soap.map((item) => ({
      ...item,
      dateUtcMinute: (item.effective_datetime || "").slice(0, 16),
      subjective: normalizeText(item.subjective),
      objective: normalizeText(item.objective),
      assessment: normalizeText(item.assessment),
      plan: normalizeText(item.plan),
    })),
  };
};

// --- Diff ----------------------------------------------------------------

const SOAP_FIELDS = ["subjective", "objective", "assessment", "plan"];

const diffEntry = (webEntry, apiEntry) => {
  const differences = [];
  for (const field of SOAP_FIELDS) {
    if ((webEntry.soap[field] || "") !== (apiEntry[field] || "")) {
      differences.push(field);
    }
  }
  return differences;
};

const buildReport = (webEntries, apiSoap, noRegistrasi) => {
  const webSoapRows = webEntries.filter(
    (entry) => entry.category === "SOAP" && entry.regno === noRegistrasi,
  );
  const webByRole = {};
  for (const entry of webSoapRows) {
    webByRole[entry.role] = (webByRole[entry.role] || 0) + 1;
  }

  // Multiple doctors can write a SOAP entry in the same displayed minute
  // (web has no seconds), so matching must happen per-minute-bucket with a
  // best-effort assignment rather than a 1:1 map (which would silently drop
  // one side on collision).
  const groupByMinute = (items, minuteOf) => {
    const groups = new Map();
    for (const item of items) {
      const key = minuteOf(item);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    return groups;
  };

  const webByMinute = groupByMinute(webSoapRows, (e) => e.dateUtcMinute);
  const apiByMinute = groupByMinute(apiSoap, (e) => e.dateUtcMinute);

  const matches = [];
  const webOnly = [];
  const apiOnly = [];

  for (const [minute, webGroup] of webByMinute) {
    const apiGroup = (apiByMinute.get(minute) || []).slice();
    const remainingWeb = webGroup.slice();

    // Greedily pair whichever (web, api) combination currently has the
    // fewest field differences, removing both sides each round.
    while (remainingWeb.length > 0 && apiGroup.length > 0) {
      let best = null;
      for (const webEntry of remainingWeb) {
        for (const apiEntry of apiGroup) {
          const differences = diffEntry(webEntry, apiEntry);
          if (!best || differences.length < best.differences.length) {
            best = { webEntry, apiEntry, differences };
          }
        }
      }
      matches.push({
        web: best.webEntry,
        api: best.apiEntry,
        differences: best.differences,
      });
      remainingWeb.splice(remainingWeb.indexOf(best.webEntry), 1);
      apiGroup.splice(apiGroup.indexOf(best.apiEntry), 1);
    }

    webOnly.push(...remainingWeb);
    apiOnly.push(...apiGroup);
  }

  for (const [minute, apiGroup] of apiByMinute) {
    if (!webByMinute.has(minute)) apiOnly.push(...apiGroup);
  }

  const lines = [];
  lines.push(`# Perbandingan CPPT — ${noRegistrasi}`);
  lines.push("");
  lines.push(`Web (grid SOAP) per role: ${JSON.stringify(webByRole)}`);
  lines.push(`API cppt.soap: ${apiSoap.length} entri`);
  lines.push("");
  lines.push(
    `## Matched (web DTR row <-> API soap entry, by timestamp UTC): ${matches.length}`,
  );
  for (const { web, api, differences } of matches) {
    const status = differences.length === 0 ? "IDENTIK" : `BEDA (${differences.join(", ")})`;
    lines.push(
      `- ${web.dateLocal} WIB (role ${web.role}, performer_id ${api.performer_id}) — ${status}`,
    );
    for (const field of differences) {
      lines.push(`    web.${field}: ${JSON.stringify(web.soap[field])}`);
      lines.push(`    api.${field}: ${JSON.stringify(api[field])}`);
    }
  }
  lines.push("");
  lines.push(
    `## Web-only (ada di grid, tidak ketemu pasangan di API): ${webOnly.length}`,
  );
  for (const entry of webOnly) {
    lines.push(
      `- ${entry.dateLocal} WIB, role ${entry.role}, performer "${entry.performer2}"`,
    );
  }
  lines.push("");
  lines.push(
    `## API-only (ada di API, tidak ketemu baris web pada menit yang sama): ${apiOnly.length}`,
  );
  for (const entry of apiOnly) {
    lines.push(`- ${entry.effective_datetime} (performer_id ${entry.performer_id})`);
  }

  return lines.join("\n");
};

// --- Main ------------------------------------------------------------------

(async () => {
  const [detailUrlArg, startDateArg, endDateArg] = process.argv.slice(2);
  const detailUrl = detailUrlArg || DEFAULT_DETAIL_URL;
  const noRegistrasi = extractNoRegistrasi(detailUrl);
  const startDate = startDateArg || deriveStartDate(noRegistrasi) || todayIso();
  const endDate = endDateArg || todayIso();

  console.error(`[info] noRegistrasi = ${noRegistrasi}`);
  console.error(`[info] range API    = ${startDate}..${endDate}`);

  console.error("[1/3] scraping web...");
  const bodyText = await scrapeWeb(detailUrl);
  const webEntries = parseWebEntries(bodyText);
  fs.writeFileSync(
    path.join(OUTPUT_DIR, "web_entries.json"),
    JSON.stringify(webEntries, null, 2),
  );

  console.error("[2/3] fetching API...");
  const { raw, soap } = await fetchApiSoap(noRegistrasi, startDate, endDate);
  fs.writeFileSync(
    path.join(OUTPUT_DIR, "api_cppt_raw.json"),
    JSON.stringify(raw, null, 2),
  );

  console.error("[3/3] comparing...");
  const report = buildReport(webEntries, soap, noRegistrasi);
  fs.writeFileSync(path.join(OUTPUT_DIR, "compare_report.md"), report);

  console.log(report);
})().catch((err) => {
  console.error("[fatal]", err);
  process.exitCode = 1;
});
