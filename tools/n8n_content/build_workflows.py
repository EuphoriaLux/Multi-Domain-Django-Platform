"""Build credential-free n8n v2 imports from versioned editorial topic selectors."""

import json
from pathlib import Path

ROOT = Path(__file__).parent


def node(name, kind, parameters, x=0, credential=None, version=1):
    value = {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": kind,
        "typeVersion": version,
        "position": [x, 0],
        "parameters": parameters,
    }
    if credential:
        value["credentials"] = credential
    return value


def http(name, url, body, credential, x=0, timeout=30000, retry=False):
    value = node(
        name,
        "n8n-nodes-base.httpRequest",
        {
            "method": "POST",
            "url": url,
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": body,
            "options": {"timeout": timeout},
        },
        x,
        {"httpHeaderAuth": {"name": credential}},
        4.2,
    )
    if retry:
        value.update(retryOnFail=True, maxTries=3, waitBetweenTries=3000)
    return value


def code(name, js, x=0):
    return node(name, "n8n-nodes-base.code", {"jsCode": js}, x, version=2)


def connect(wf, source, target, kind="main"):
    wf["connections"][source] = {kind: [[{"node": target, "type": kind, "index": 0}]]}


def workflow(name, error_id="__ERROR_WORKFLOW_ID__"):
    return {
        "name": name,
        "active": False,
        "nodes": [],
        "connections": {},
        "settings": {
            "executionOrder": "v1",
            "timezone": "Europe/Luxembourg",
            "executionTimeout": 240,
            "saveDataErrorExecution": "all",
            "saveDataSuccessExecution": "none",
            "saveManualExecutions": False,
            "errorWorkflow": error_id,
        },
    }


PROMPT = """You write useful, honest Crush.lu coaching content for adults 30–49 in Luxembourg.
Return ONLY a JSON object. No markdown fences, preamble, emojis or fabricated stats.
English is primary. French captions always use vous. Never claim zero fake profiles,
guaranteed safety, sold-out seats, prices, member counts or an available founding offer
without an explicitly provided dated source. LuxID verifies identity, not behaviour.
Describe suggested local walks or cafes as suggestions; never invent events or testimonials.
Respect boundaries; avoid diagnostic or therapeutic promises and pressure to date.
Each advice slide adds one practical idea. Keep titles under 80 characters, body under
200 characters, captions under 1400 characters INCLUDING CTA and 3–5 hashtags.
CTA URL: https://crush.lu/en/ (FR: https://crush.lu/fr/). Do not invent deep links.
Schema: {\"slides\":[{\"title\":\"...\",\"body\":\"...\"}],\"caption_en\":\"...\",
\"caption_fr\":\"...\",\"visualPrompt\":\"...\"}.
The cover artwork must preserve the supplied reference ghost proportions: matte white,
purple eyes, blush cheeks, pink heart. Use an authentic Luxembourg scene. No text, logos
or badges in the generated artwork; deterministic overlays supply those.
"""

VALIDATE = r"""const raw = $json.output || $json.text;
if (typeof raw !== 'string') throw new Error('LLM returned no text');
let parsed; try { parsed = JSON.parse(raw); } catch { throw new Error('Copy must be strict JSON; repair the prompt before sending'); }
const selected = $('Select Topic').first().json;
const count = selected.kind === 'carousel' ? 5 : 1;
if (!Array.isArray(parsed.slides) || parsed.slides.length !== count) throw new Error('Incomplete slide script');
for (const s of parsed.slides) {
  if (typeof s.title !== 'string' || !s.title.trim() || s.title.length > 100 || typeof s.body !== 'string' || s.body.length > 300) throw new Error('Slide text exceeds readable limits');
}
for (const key of ['caption_en','caption_fr']) {
  if (typeof parsed[key] !== 'string' || !parsed[key].trim() || parsed[key].length > 1500) throw new Error('Caption exceeds Hub 1500 / Instagram 2199 / LinkedIn 3000 limits');
  if ((parsed[key].match(/#[\p{L}\p{N}_]+/gu)||[]).length > 5) throw new Error('Too many hashtags');
}
if (typeof parsed.visualPrompt !== 'string' || !parsed.visualPrompt.trim() || parsed.visualPrompt.length > 2000) throw new Error('Invalid image prompt');
const script = {slides:parsed.slides, caption_en:parsed.caption_en, caption_fr:parsed.caption_fr,
  visualPrompt:parsed.visualPrompt, ghostScene:selected.ghostScene, language:'en', pillar:selected.hubPillar};
return [{json:{script,run_key:`${$workflow.id}:${$execution.id}`}}];
"""


def rendering_nodes(wf, script_node, regeneration=False):
    n = wf["nodes"]
    n.append(
        http(
            "Load Mascot Reference",
            "http://content-renderer:8092/reference",
            f"={{{{ {{ ghostScene: $('{script_node}').first().json.script.ghostScene }} }}}}",
            "Crush Renderer Bearer",
            800,
            retry=True,
        )
    )
    n.append(
        code(
            "Prepare Reference Binary",
            """const raw = $json.image_base64;
if (!raw) throw new Error('Renderer returned no reference');
const reference = await this.helpers.prepareBinaryData(Buffer.from(raw,'base64'), 'reference.png', 'image/png');
return [{json:$json, binary:{reference}}];""",
            1000,
        )
    )
    visual = f"={{{{ $('{script_node}').first().json.script.visualPrompt + ' Preserve the reference mascot exactly. No letters, logos or watermark. Square composition.' "
    if regeneration:
        visual += "+ ' Create a new composition and lighting; retain character identity. Variation ' + $execution.id "
    visual += "}}"
    n.append(
        node(
            "Generate Grounded Cover",
            "@n8n/n8n-nodes-langchain.googleGemini",
            {
                "resource": "image",
                "operation": "edit",
                "modelId": {
                    "__rl": True,
                    "value": "models/gemini-2.5-flash-image",
                    "mode": "list",
                },
                "prompt": visual,
                "images": {"values": [{"binaryPropertyName": "reference"}]},
                "options": {"binaryPropertyOutput": "cover"},
            },
            1200,
            {"googlePalmApi": {"name": "Crush Gemini API"}},
            1,
        )
    )
    n.append(
        code(
            "Prepare Render Request",
            f"""const cover = await this.helpers.getBinaryDataBuffer(0, 'cover');
const previous = $('{script_node}').first().json;
return [{{json:{{...previous,cover_base64:cover.toString('base64')}}}}];""",
            1400,
        )
    )
    n.append(
        http(
            "Render Complete Deck",
            "http://content-renderer:8092/render",
            "={{ {script:$json.script,cover_base64:$json.cover_base64} }}",
            "Crush Renderer Bearer",
            1600,
            60000,
            True,
        )
    )
    n.append(
        http(
            "Store and Send Review",
            "http://content-review:8093/deliver",
            f"={{{{ {{ script: $('{script_node}').first().json.script, images:$json.images, run_key: `${{$workflow.id}}:${{$execution.id}}`, "
            + (
                f"review_id:$('{script_node}').first().json.review_id"
                if regeneration
                else "review_id:null"
            )
            + " } }}",
            "Crush Review Bearer",
            1800,
            180000,
        )
    )
    names = [
        script_node,
        "Load Mascot Reference",
        "Prepare Reference Binary",
        "Generate Grounded Cover",
        "Prepare Render Request",
        "Render Complete Deck",
        "Store and Send Review",
    ]
    for source, target in zip(names, names[1:]):
        connect(wf, source, target)


for carousel in (False, True):
    kind = "carousel" if carousel else "editorial"
    wf = workflow(
        f"Crush.lu v2 - {'Five-slide coaching carousel' if carousel else 'Editorial review'}"
    )
    array_name = "themes" if carousel else "pillars"
    selector = (ROOT / "selectors" / f"{kind}.js").read_text(encoding="utf-8")
    # Correct known unsupported promises even before the LLM sees the guidance.
    selector = selector.replace(
        "no fake profiles", "identity verification helps reduce impersonation"
    )
    selector = selector.replace("zero fake profiles", "identity verification")
    selector = selector.replace(
        "ticket urgency, balanced ratios",
        "published event details only; no invented urgency or ratios",
    )
    selector = selector.replace(
        "Claim your founding member invite on crush.lu",
        "Explore the Crush.lu community at https://crush.lu/en/",
    )
    selector = selector.replace("crush.lu/events/", "https://crush.lu/en/")
    selector += f"""
const now = new Date();
const local = new Date(now.toLocaleString('en-US', {{timeZone:'Europe/Luxembourg'}}));
const day = local.getDay();
const week = Math.floor((Date.UTC(local.getFullYear(),local.getMonth(),local.getDate()) - Date.UTC(2026,8,28))/(7*86400000));
const slot = week*2 + (day >= {5 if carousel else 4} ? 1 : 0);
const override = $json.body?.topic;
const selected = override ? {array_name}.find(t=>t.id===override) : {array_name}[((slot%{array_name}.length)+{array_name}.length)%{array_name}.length];
if(!selected) throw new Error('Unknown topic');
const mapping = {{events:'promo',proof:'milestone',coach_tips:'dating_tip',trust:'community',founding_members:'community'}};
"""
    selector += (
        "return [{json:{...selected,kind:"
        + json.dumps(kind)
        + ",hubPillar:"
        + ("'dating_tip'" if carousel else "mapping[selected.id]")
        + "}}];"
    )
    wf["nodes"] = [
        node("Manual Generate Review", "n8n-nodes-base.manualTrigger", {}),
        node(
            "Twice Weekly",
            "n8n-nodes-base.scheduleTrigger",
            {
                "rule": {
                    "interval": [
                        {
                            "field": "weeks",
                            "triggerAtDay": [2, 5] if carousel else [1, 4],
                            "triggerAtHour": 10 if carousel else 9,
                            "triggerAtMinute": 0 if carousel else 30,
                        }
                    ]
                }
            },
            version=1.2,
        ),
        node(
            "Authenticated Generate",
            "n8n-nodes-base.webhook",
            {
                "httpMethod": "POST",
                "path": f"crush-{kind}-generate-v2",
                "authentication": "headerAuth",
                "responseMode": "onReceived",
                "options": {},
            },
            credential={"httpHeaderAuth": {"name": "Crush Generation Webhook"}},
            version=2,
        ),
        code("Select Topic", selector, 200),
        node(
            "Generate Structured Copy",
            "@n8n/n8n-nodes-langchain.agent",
            {
                "promptType": "define",
                "text": "="
                + PROMPT
                + "\nTopic: {{ $json.name }}\nGuidance: {{ $json.description }}\n"
                + (
                    "Make exactly FIVE slides: hook, struggle, mindset, practical action, CTA.\n"
                    if carousel
                    else "Make exactly ONE slide: hook and short body.\n"
                )
                + "Mascot idea: {{ $json.mascotAction }}",
                "options": {"maxIterations": 1},
            },
            400,
            version=1.7,
        ),
        node(
            "Subscription Chat Model",
            "@n8n/n8n-nodes-langchain.lmChatOpenAi",
            {
                "model": {"__rl": True, "value": "gpt-6-luna", "mode": "list"},
                "responsesApiEnabled": False,
                "options": {"timeout": 45000, "maxRetries": 0, "maxTokens": 2500},
            },
            400,
            {"openAiApi": {"name": "Crush Subscription Bridge"}},
            1.3,
        ),
        code("Validate Script", VALIDATE, 600),
    ]
    for trigger in ["Manual Generate Review", "Twice Weekly", "Authenticated Generate"]:
        connect(wf, trigger, "Select Topic")
    connect(wf, "Select Topic", "Generate Structured Copy")
    connect(
        wf, "Subscription Chat Model", "Generate Structured Copy", "ai_languageModel"
    )
    connect(wf, "Generate Structured Copy", "Validate Script")
    rendering_nodes(wf, "Validate Script")
    (ROOT / f"{kind}.workflow.json").write_text(
        json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8"
    )

regen = workflow("Crush.lu v2 - Regenerate reviewed cover")
regen["nodes"] = [
    node(
        "Regenerate Webhook",
        "n8n-nodes-base.webhook",
        {
            "httpMethod": "POST",
            "path": "crush-visual-regenerate-v2",
            "authentication": "headerAuth",
            "responseMode": "onReceived",
            "options": {},
        },
        credential={"httpHeaderAuth": {"name": "Crush Regeneration Webhook"}},
        version=2,
    ),
    code(
        "Validate Review ID",
        """const id=$json.body?.review_id;
if(!/^[0-9a-f]{32}$/.test(id || '')) throw new Error('Invalid review ID');
return [{json:{review_id:id}}];""",
        200,
    ),
]
getter = node(
    "Load Review",
    "n8n-nodes-base.httpRequest",
    {
        "url": "={{ 'http://content-review:8093/reviews/' + $json.review_id }}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {"timeout": 15000},
    },
    400,
    {"httpHeaderAuth": {"name": "Crush Review Bearer"}},
    4.2,
)
regen["nodes"].append(getter)
connect(regen, "Regenerate Webhook", "Validate Review ID")
connect(regen, "Validate Review ID", "Load Review")
rendering_nodes(regen, "Load Review", True)
(ROOT / "regenerate.workflow.json").write_text(
    json.dumps(regen, indent=2), encoding="utf-8"
)

error = workflow("Crush.lu v2 - Durable error inbox", None)
error["settings"].pop("errorWorkflow")
error["nodes"] = [
    node("Error Trigger", "n8n-nodes-base.errorTrigger", {}),
    code(
        "Sanitize Error Envelope",
        r"""const execution=$json.execution || {}, error=execution.error || $json.trigger?.error || {};
return [{json:{workflow:$json.workflow?.name || 'unknown',execution:execution.id || 'trigger-failure',
node:execution.lastNodeExecuted || error.node?.name || 'trigger',message:error.message || 'Workflow failed',stack:error.stack || ''}}];""",
        200,
    ),
    http(
        "Persist Dead Letter",
        "http://content-review:8093/errors",
        "={{ $json }}",
        "Crush Review Bearer",
        400,
        retry=True,
    ),
]
connect(error, "Error Trigger", "Sanitize Error Envelope")
connect(error, "Sanitize Error Envelope", "Persist Dead Letter")
(ROOT / "errors.workflow.json").write_text(
    json.dumps(error, indent=2), encoding="utf-8"
)
print("Built four inactive credential-free workflow definitions.")
