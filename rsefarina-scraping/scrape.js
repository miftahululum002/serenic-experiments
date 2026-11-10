"use strict";

const puppeteer = require("puppeteer");

const BASE_URL = "http://36.64.110.114:8083";
const USERNAME = "serenic";
const PASSWORD = "3krlinc1P3nan3";

const DETAIL_URL =
  BASE_URL +
  "/Live2/Module/RADT/EmrIp/EmrIpDetail.aspx?rt=IPR&regno=REG/IP/261013-0044&fregno=REG/EM/261013-0043&parid=MD-00268&unit=D22&room=2203&patid=P20261013-0029";

(async () => {
  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1366, height: 900 });

    const requestLog = [];
    page.on("request", (req) => {
      requestLog.push({
        method: req.method(),
        url: req.url(),
        resourceType: req.resourceType(),
        postData: req.postData() || null,
      });
    });

    console.error(`[nav] ${DETAIL_URL}`);
    await page.goto(DETAIL_URL, { waitUntil: "networkidle2", timeout: 30000 });
    console.error(`[url after first nav] ${page.url()}`);

    if (page.url().includes("Login.aspx")) {
      console.error("[login] filling credentials");
      await page.waitForSelector("#ctl00_ContentPlaceHolder1_txtUserID", {
        timeout: 15000,
      });
      await page.type("#ctl00_ContentPlaceHolder1_txtUserID", USERNAME, {
        delay: 20,
      });
      await page.type("#ctl00_ContentPlaceHolder1_txtPassword", PASSWORD, {
        delay: 20,
      });

      await Promise.all([
        page.waitForNavigation({ waitUntil: "networkidle2", timeout: 30000 }),
        page.click("#ctl00_ContentPlaceHolder1_btnLogin_input"),
      ]);
      console.error(`[url after login] ${page.url()}`);

      if (!page.url().includes("EmrIpDetail.aspx")) {
        console.error("[nav] re-navigating to detail URL after login");
        await page.goto(DETAIL_URL, {
          waitUntil: "networkidle2",
          timeout: 30000,
        });
        console.error(`[url after re-nav] ${page.url()}`);
      }
    } else {
      console.error("[login] skipped, already authenticated");
    }

    const title = await page.title();
    console.error(`[title] ${title}`);

    const bodyText = await page.evaluate(() => document.body.innerText);
    console.log(bodyText);

    const fs = require("fs");
    fs.writeFileSync(
      "/Users/miftahululum002/projects/serenic/experiments/rsefarina-scraping/output_detail.html",
      await page.content(),
    );
    console.error(
      "[saved] output_detail.html",
    );

    fs.writeFileSync(
      "/Users/miftahululum002/projects/serenic/experiments/rsefarina-scraping/request_log.json",
      JSON.stringify(requestLog, null, 2),
    );
    console.error(`[saved] request_log.json (${requestLog.length} requests)`);
  } finally {
    await browser.close();
  }
})();
