const http = require('node:http');
const crypto = require('node:crypto');
const {renderDeck,reference}=require('./renderer.cjs');
const token=process.env.RENDERER_TOKEN;
if (!token || token.length<32) throw new Error('Set a renderer credential of at least 32 characters');
const assets=process.env.ASSET_DIR || '/assets';
let busy=false;
function authorized(header) {
  const supplied=Buffer.from(header || ''), expected=Buffer.from(`Bearer ${token}`);
  return supplied.length===expected.length && crypto.timingSafeEqual(supplied,expected);
}
http.createServer(async (req,res)=>{
  const reply=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(req.url==='/healthz' && req.method==='GET') return reply(200,{status:'ok'});
  if(!authorized(req.headers.authorization)) return reply(401,{error:'Unauthorized'});
  if(req.method!=='POST' || !['/reference','/render'].includes(req.url)) return reply(404,{error:'Unknown operation'});
  if(busy) return reply(429,{error:'Renderer busy; retry later'});
  busy=true;
  try {
    let size=0; const chunks=[];
    for await(const chunk of req) {size+=chunk.length;if(size>12*1024*1024) throw new Error('Request too large');chunks.push(chunk);}
    const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if(req.url==='/reference') return reply(200,{image_base64:(await reference(assets,body.ghostScene)).toString('base64')});
    const images=await renderDeck(body.script,Buffer.from(body.cover_base64 || '', 'base64'),assets);
    reply(200,{images});
  } catch(error) {reply(422,{error:error.message});} finally {busy=false;}
}).listen(Number(process.env.PORT || 8092),'0.0.0.0');
