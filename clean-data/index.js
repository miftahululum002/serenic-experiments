import * as cheerio from 'cheerio';
const htmlContent = `<p style="line-height: 0.5;">Pada pemeriksaan foto Thorax : </p><p style="line-height: 0.5;">
</p><p style="line-height: 0.5;">Skeletal dan soft tissue yang tervisualisasi dalam batas normal</p><p style="line-height: 0.5;">Trakea di tengah</p><p style="line-height: 0.5;">Cor tidak membesar.</p><p style="line-height: 0.5;">Sinuses dan diafragma normal</p><p style="line-height: 0.5;">Pulmo:</p><p style="line-height: 0.5;">-       Hili normal.</p><p style="line-height: 0.5;">-       Corakan bronkovaskuler bertambah, cuffing sign (+), tram line (+).</p><p style="line-height: 0.5;">-       Tampak infiltrat di lapang bawah paru bilateral</p><p style="line-height: 0.5;">
</p><p style="line-height: 0.5;">
</p><p style="line-height: 0.5;">
</p><p style="line-height: 0.5;">KESAN : </p><p style="line-height: 0.5;">Gambaran Bronkitis disertai infeksi sekunder</p><p style="line-height: 0.5;">Tidak tampak kardiomegali</p>`;

const $ = cheerio.load(htmlContent);

// Mengambil teks dari seluruh elemen <p>, membuang baris kosong, lalu menggabungkannya
const text = $('p')
  .map((i, el) => $(el).text().trim())
  .get()
  .filter(Boolean)
  .join('\n');

console.log(text);