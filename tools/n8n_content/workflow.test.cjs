const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
for(const kind of ['editorial','carousel','regenerate','errors']) {
  test(`${kind} workflow has valid edges, code and no embedded secrets`,()=>{
    const wf=JSON.parse(fs.readFileSync(path.join(__dirname,`${kind}.workflow.json`)));
    assert.equal(wf.active,false);
    const names=new Set(wf.nodes.map(n=>n.name));
    for(const [source,edges] of Object.entries(wf.connections)) {
      assert.ok(names.has(source));
      for(const outputs of Object.values(edges)) {
        assert.equal(outputs.length,1,'Only actual output zero is connected');
        for(const branch of outputs) for(const edge of branch) assert.ok(names.has(edge.node));
      }
    }
    for(const node of wf.nodes) {
      if(node.parameters.jsCode) assert.doesNotThrow(()=>new AsyncFunction(node.parameters.jsCode));
      for(const value of Object.values(node.parameters)) {
        if(typeof value==='string' && value.startsWith('={{') && value.endsWith('}}')) {
          assert.doesNotThrow(()=>new Function('$json','$','$workflow','$execution',`return (${value.slice(3,-2)});`));
        }
      }
      if(node.type==='n8n-nodes-base.webhook') assert.equal(node.parameters.authentication,'headerAuth');
      if(node.name==='Store and Send Review') assert.notEqual(node.retryOnFail,true,'External writes must not blindly retry');
      assert.equal(node.parameters.headerParameters,undefined,'Use credential references');
    }
  });
}
test('caption and slide guardrails reject bad output before any image or publication',async()=>{
  const wf=JSON.parse(fs.readFileSync(path.join(__dirname,'carousel.workflow.json')));
  const run=new AsyncFunction('$json','$','$workflow','$execution',wf.nodes.find(n=>n.name==='Validate Script').parameters.jsCode);
  const selected={kind:'carousel',ghostScene:'ghost_scene_phone.svg',hubPillar:'dating_tip'};
  const payload={slides:Array.from({length:5},()=>({title:'Try a short walk',body:'Ask one open question.'})),caption_en:'Tip #Luxembourg',caption_fr:'Conseil #Luxembourg',visualPrompt:'Crushy in Luxembourg'};
  const invoke=p=>run({output:JSON.stringify(p)},()=>({first:()=>({json:selected})}),{id:'wf'},{id:'42'});
  assert.equal((await invoke(payload))[0].json.script.slides.length,5);
  await assert.rejects(()=>invoke({...payload,caption_en:'x'.repeat(1501)}),/Caption/);
  await assert.rejects(()=>invoke({...payload,slides:payload.slides.slice(0,4)}),/Incomplete/);
});
