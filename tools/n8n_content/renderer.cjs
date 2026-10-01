// Deterministic text and brand overlays. Gemini generates only the cover artwork.
const sharp = require('sharp');
const fs = require('node:fs/promises');
const path = require('node:path');
const SIZE = 1080;
const SCENES = new Set(['chatting_pair','phone','discover','party','approved','unlock','inlove']);
function sceneName(value) {
  const match = /^ghost_scene_([a-z_]+)\.svg$/.exec(value || '');
  if (!match || !SCENES.has(match[1])) throw new Error('Unapproved mascot scene');
  return value;
}
const xml = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
// Conservative glyph widths keep accented FR and DE text within the card.
function width(text, size) {
  return [...text].reduce((sum,c) => sum + (/\s/.test(c) ? .34 : /[MW@Œ]/.test(c) ? 1.05 : /[ilI.,:;'!]/.test(c) ? .36 : .69)*size, 0);
}
function wrap(text, size, maxWidth=864) {
  const lines = []; let line = '';
  for (const word of text.trim().split(/\s+/)) {
    if (width(word,size) > maxWidth) throw new Error('Unbreakable word exceeds card width');
    const next = line ? `${line} ${word}` : word;
    if (width(next,size) > maxWidth) { lines.push(line); line = word; } else line = next;
  }
  if (line) lines.push(line);
  return lines;
}
function fit(text, start, min, maxLines) {
  for (let size=start; size>=min; size-=2) {
    try { const lines=wrap(text,size); if(lines.length <= maxLines) return {lines,size}; } catch {}
  }
  throw new Error('Copy exceeds the readable card area; shorten it');
}
function textBlock(fitted, y, color, weight='normal') {
  return fitted.lines.map((line,i)=>`<text x="108" y="${y+i*fitted.size*1.24}" font-family="DejaVu Sans" font-size="${fitted.size}" font-weight="${weight}" fill="${color}">${xml(line)}</text>`).join('');
}
function validateScript(script) {
  if (!script || ![1,5].includes(script.slides?.length)) throw new Error('Expected one editorial image or exactly five carousel slides');
  sceneName(script.ghostScene);
  if (!['fr','en'].includes(script.language)) throw new Error('Unsupported language');
  for (const slide of script.slides) {
    if (typeof slide.title !== 'string' || !slide.title.trim() || slide.title.length > 100) throw new Error('Slide title must contain 1–100 characters');
    if (typeof slide.body !== 'string' || slide.body.length > 300) throw new Error('Slide body must be at most 300 characters');
    fit(slide.title,64,42,3); fit(slide.body,40,34,6);
  }
  for (const name of ['caption_en','caption_fr']) {
    if (typeof script[name] !== 'string' || !script[name].trim() || script[name].length > 1500) throw new Error('Captions must fit the Hub 1500-character ceiling');
    if ((script[name].match(/#[\p{L}\p{N}_]+/gu)||[]).length > 5) throw new Error('Use at most five relevant hashtags');
  }
  if (typeof script.visualPrompt !== 'string' || !script.visualPrompt.trim() || script.visualPrompt.length > 2000) throw new Error('Invalid visual prompt');
  return script;
}
async function reference(assets, name) {
  const source=await fs.readFile(path.join(assets,sceneName(name)));
  // Assets are trusted, read-only SVGs; request input can never supply SVG markup.
  return sharp(source).resize(640,640,{fit:'contain',background:'#00000000'}).png().toBuffer();
}
async function renderDeck(script, cover, assets) {
  validateScript(script);
  if (!Buffer.isBuffer(cover) || !cover.length || cover.length > 8*1024*1024) throw new Error('Invalid cover image');
  const mascot=await reference(assets,script.ghostScene);
  const icon=await sharp(path.join(assets,'crush_watermark_140.png')).resize(88,88,{fit:'contain',background:'#00000000'}).png().toBuffer();
  const circle=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="130" height="130"><circle cx="65" cy="65" r="62" fill="#27183f" fill-opacity=".85" stroke="#FF5CA9" stroke-width="3"/></svg>');
  const badge=await sharp(circle).composite([{input:icon,left:21,top:21}]).png().toBuffer();
  const background=await sharp(cover,{limitInputPixels:20_000_000}).rotate().resize(SIZE,SIZE,{fit:'cover'}).png().toBuffer();
  const result=[];
  for (let i=0;i<script.slides.length;i++) {
    const slide=script.slides[i], title=fit(slide.title,64,42,3), body=fit(slide.body,40,34,6);
    const titleY=238, bodyY=titleY+(title.lines.length-1)*title.size*1.24+85;
    if (bodyY+(body.lines.length-1)*body.size*1.24 > 685) throw new Error('Slide text collides with mascot; shorten it');
    const art=i===0 ? '' : `<image x="635" y="690" width="280" height="280" href="data:image/png;base64,${mascot.toString('base64')}"/>`;
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080">
      <rect width="1080" height="1080" fill="#1e1333" fill-opacity="${i===0?'.12':'1'}"/>
      ${i===0?'<rect x="70" y="170" width="940" height="510" rx="32" fill="#1e1333" fill-opacity=".88"/>':''}
      <rect x="70" y="70" width="940" height="940" rx="32" fill="none" stroke="#FF5CA9" stroke-width="2"/>
      <text x="108" y="144" font-family="DejaVu Sans" font-size="26" fill="#FF5CA9" font-weight="bold">CRUSH.LU · ${script.slides.length===5?'COACHING':'LUXEMBOURG'}</text>
      <text x="904" y="144" font-family="DejaVu Sans" font-size="26" fill="#ffffff">${String(i+1).padStart(2,'0')}/${String(script.slides.length).padStart(2,'0')}</text>
      ${textBlock(title,titleY,'#ffffff','bold')}${textBlock(body,bodyY,'#E0D6EB')}${art}
      <rect x="108" y="850" width="88" height="5" rx="2" fill="#FF5CA9"/>
      <text x="108" y="928" font-family="DejaVu Sans" font-size="28" fill="#ffffff">crush.lu</text>
    </svg>`;
    const overlays=[{input:Buffer.from(svg),left:0,top:0},{input:badge,left:930,top:930}];
    const png=await sharp(background).composite(overlays).png().toBuffer();
    result.push({image_base64:png.toString('base64'),mime_type:'image/png',filename:`slide-${String(i+1).padStart(2,'0')}.png`});
  }
  return result;
}
module.exports={renderDeck,reference,validateScript,wrap,fit,sceneName};
