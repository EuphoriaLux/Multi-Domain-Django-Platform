const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
for(const kind of ['editorial','carousel','regenerate','errors','ideas']) {
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

test('source selection precedes copy; history is consumed after successful delivery',()=>{
  for(const kind of ['editorial','carousel']) {
    const wf=JSON.parse(fs.readFileSync(path.join(__dirname,`${kind}.workflow.json`)));
    assert.equal(wf.connections['Apply Sourced Idea'].main[0][0].node,'Generate Structured Copy');
    assert.equal(wf.connections['Store and Send Review'].main[0][0].node,'Record Reviewed Idea');
    assert.equal(wf.nodes.find(n=>n.name==='Select Sourced Idea').onError,'continueRegularOutput');
    assert.equal(wf.nodes.find(n=>n.name==='Record Reviewed Idea').onError,'continueRegularOutput');
  }
});

test('explicit future posting dates determine fallback slot and invalid dates fail early',async()=>{
  const wf=JSON.parse(fs.readFileSync(path.join(__dirname,'carousel.workflow.json')));
  const run=new AsyncFunction('$json',wf.nodes.find(n=>n.name==='Select Topic').parameters.jsCode);
  const future=new Date(Date.now()+12*86400000).toISOString().slice(0,10);
  const selected=(await run({body:{posting_date:future}}))[0].json;
  assert.equal(selected.postingDate,future);
  await assert.rejects(()=>run({body:{posting_date:'2026-02-30'}}),/posting_date/);
  assert.equal((await run({body:{topic:'icebreakers'}}))[0].json.skipFeed,true);
});

test('feed errors retain the original evergreen selection and review flags',async()=>{
  const wf=JSON.parse(fs.readFileSync(path.join(__dirname,'carousel.workflow.json')));
  const run=new AsyncFunction('$json','$',wf.nodes.find(n=>n.name==='Apply Sourced Idea').parameters.jsCode);
  const base={kind:'carousel',name:'Evergreen theme',postingDate:'2026-10-08',visualOnly:true};
  const result=(await run({error:'Service unavailable'},()=>({first:()=>({json:base})})))[0].json;
  assert.equal(result.name,base.name);
  assert.equal(result.visualOnly,true);
  assert.equal(result.sourceIdea,null);
});
