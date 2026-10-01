const fs=require('node:fs/promises');
const path=require('node:path');
const sharp=require('sharp');
const {renderDeck}=require('./renderer.cjs');
(async()=>{
  const script={language:'en',pillar:'dating_tip',ghostScene:'ghost_scene_chatting_pair.svg',
    caption_en:'A small question can open a real conversation. #Luxembourg #CrushLu',
    caption_fr:'Une petite question peut ouvrir une vraie conversation. #Luxembourg #CrushLu',
    visualPrompt:'Crushy at a cafe in Grund',slides:[
      {title:'Skip the perfect opening line.',body:'Start a conversation that feels like you.'},
      {title:'Small talk can feel like a test.',body:'When you try to impress, it gets harder to listen. You do not need a performance.'},
      {title:'Choose curiosity over chemistry.',body:'You can learn one thing about someone without deciding where it will lead.'},
      {title:'Try one question this week.',body:'“What is one place in Luxembourg you would happily visit again?” Then ask what made it memorable.'},
      {title:'Keep the next step small.',body:'If the conversation feels mutual, suggest a short coffee or walk. A warm no is also a clear answer.'}]};
  const assets=path.join(__dirname,'preview-assets'), output=path.join(__dirname,'preview');
  const scene=await sharp(path.join(assets,'ghost_scene_chatting_pair.svg')).resize(480,480,{fit:'contain',background:'#00000000'}).png().toBuffer();
  const gradient=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080"><defs><radialGradient id="g"><stop stop-color="#64244c"/><stop offset="1" stop-color="#1e1333"/></radialGradient></defs><rect width="1080" height="1080" fill="url(#g)"/></svg>');
  const cover=await sharp(gradient).composite([{input:scene,left:480,top:555}]).png().toBuffer();
  const images=await renderDeck(script,cover,assets);
  await fs.mkdir(output,{recursive:true});
  await fs.writeFile(path.join(output,'script.json'),JSON.stringify(script,null,2));
  const tiles=[];
  for(let i=0;i<images.length;i++) {
    const raw=Buffer.from(images[i].image_base64,'base64');
    await fs.writeFile(path.join(output,images[i].filename),raw);
    tiles.push({input:await sharp(raw).resize(360,360).png().toBuffer(),left:i*360,top:0});
  }
  await sharp({create:{width:1800,height:360,channels:3,background:'white'}}).composite(tiles).png().toFile(path.join(output,'contact-sheet.png'));
  console.log('Saved five brand-asset previews and contact sheet. This deterministic preview contains no new Gemini output.');
})();
