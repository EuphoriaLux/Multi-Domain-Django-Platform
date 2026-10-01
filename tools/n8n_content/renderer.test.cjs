const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const sharp=require('sharp');
const {renderDeck,validateScript,sceneName}=require('./renderer.cjs');
function script() {return {slides:Array.from({length:5},(_,i)=>({title:`Conseil ${i+1} : osez une vraie conversation`,body:'Une question ouverte, puis le temps d’écouter. Respectez votre rythme et celui de l’autre.'})),caption_en:'A small step. #Luxembourg',caption_fr:'Un petit pas. #Luxembourg',visualPrompt:'Crushy in Grund',ghostScene:'ghost_scene_chatting_pair.svg',language:'fr',pillar:'dating_tip'};}
test('complete deck has five distinct 1080px PNGs with safe FR typography',async()=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'crush-render-'));
  try {
    await fs.writeFile(path.join(dir,'ghost_scene_chatting_pair.svg'),'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="40" fill="white"/></svg>');
    const cover=await sharp({create:{width:1024,height:1024,channels:3,background:'#805080'}}).png().toBuffer();
    await fs.writeFile(path.join(dir,'crush_watermark_140.png'),await sharp({create:{width:140,height:140,channels:4,background:'#ff5ca9'}}).png().toBuffer());
    const images=await renderDeck(script(),cover,dir);
    assert.equal(images.length,5);assert.equal(new Set(images.map(x=>x.image_base64)).size,5);
    for(const image of images) {const m=await sharp(Buffer.from(image.image_base64,'base64')).metadata();assert.equal(m.width,1080);assert.equal(m.height,1080);assert.equal(m.format,'png');}
    const another=await sharp({create:{width:1080,height:1080,channels:3,background:'#336644'}}).png().toBuffer();
    const regenerated=await renderDeck(script(),another,dir);
    assert.notEqual(regenerated[0].image_base64,images[0].image_base64);
    for(let i=1;i<5;i++) assert.equal(regenerated[i].image_base64,images[i].image_base64,'Regeneration preserves advice-card pixels');
  } finally {await fs.rm(dir,{recursive:true,force:true});}
});
test('partial deck, excessive copy, and unapproved asset paths fail closed',()=>{
  const s=script();s.slides.pop();assert.throws(()=>validateScript(s),/exactly five/);
  const long=script();long.caption_fr='x'.repeat(1501);assert.throws(()=>validateScript(long),/ceiling/);
  assert.throws(()=>sceneName('../../secret.svg'),/Unapproved/);
  const many=script();many.caption_en='#a #b #c #d #e #f';assert.throws(()=>validateScript(many),/five/);
});
