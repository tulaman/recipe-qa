# Request pipeline: from question to answer

How a question travels through the service, which steps are deterministic
code and where the LLM is involved. The contract is SPEC.md §3; this page
is a picture of it and of the code in `recipe_qa/api.py` and
`recipe_qa/service.py`.

Legend: blue = deterministic code, orange = LLM call (Claude, structured
output), grey = committed data, red = HTTP error, purple = refusal
(HTTP 200, `refused: true`), green = answer.

## 1. Online: `POST /ask`

```mermaid
flowchart TD
    U(["User<br/>web UI or HTTP client"]) -->|"POST /ask {question}"| RID

    subgraph HTTP["HTTP layer - api.py"]
        RID["Assign request_id<br/>start JSON trace"]
        RL{"Rate limit<br/>30 req/min per client"}
        VAL{"Validate body<br/>≤ 8 KB, 1-1000 chars after trim"}
        CC{"Concurrency slot free?<br/>max 4 in flight"}
        RID --> RL
        RL -->|ok| VAL
        VAL -->|ok| CC
    end

    RL -->|"exceeded: 429"| ERR
    VAL -->|"invalid: 422"| ERR
    CC -->|"busy: 429"| ERR

    CC -->|ok| EX

    subgraph SVC["AskService - service.py, 12 s overall deadline"]
        EX["<b>LLM #1: extract</b><br/>input: question only<br/>prompt: prompts/extract.md<br/>output: Extraction"]
        EXOUT[/"Extraction<br/>in_domain, safety_question,<br/>max_total_minutes, time_limit_inclusive,<br/>diet, must_include, must_exclude"/]
        EX --> EXOUT

        G1{"in_domain?"}
        G2{"Asks for a safety<br/>guarantee?<br/>(safety_question)"}
        EXOUT --> G1
        G1 -->|yes| G2

        SAFE["<b>Safety refusal, no generation</b><br/>find recipes to cite:<br/>BM25 top-3 over whole corpus,<br/>no constraints applied,<br/>keep hits ≥ 50 % of best score"]
        G2 -->|"yes: refuse"| SAFE

        ELIG["is_eligible(recipe, constraints)<br/>over the whole corpus:<br/>time, diet, include, exclude,<br/>aliases from data/aliases.json"]
        G2 -->|no| ELIG
        ANYELIG{"any recipe<br/>eligible?"}
        ELIG --> ANYELIG

        BM["BM25 retrieve, k = 5<br/>over eligible recipes<br/>title x3, ingredients x2, rest x1"]
        ANYELIG -->|yes| BM
        HITS{"any recipe shares<br/>a term with the question?"}
        BM --> HITS

        CTX["Top-3 recipes rendered as context<br/>time, servings, diet with provenance,<br/>ingredients, steps, sections<br/>(nutrition hidden, &lt; &gt; escaped)"]
        HITS -->|yes| CTX

        GEN["<b>LLM #2: generate</b><br/>input: top-3 recipes + question<br/>prompt: prompts/generate.md<br/>output: Generation"]
        CTX --> GEN

        CHK{"Validate Generation<br/>answer non-empty,<br/>1-3 distinct source_ids,<br/>source_ids ⊆ given top-3"}
        GEN --> CHK
        ANS{"answerable?"}
        CHK -->|valid| ANS

        MAP["Map source_ids → title + url<br/>from manifest.json only"]
        ANS -->|yes| MAP
    end

    EX -.->|"invalid output: retry once"| EX
    CHK -.->|"invalid: retry generate once"| GEN

    R_SAFE["refused: safety<br/>answer = null<br/>citations = those 0-3 recipes"]
    SAFE --> R_SAFE
    R_OOD["refused: out_of_domain<br/>no citations"]
    R_OOC["refused: out_of_corpus<br/>no citations"]
    R_UNMET["refused: out_of_corpus<br/>message names the constraints<br/>no citations, nothing relaxed"]
    ANYELIG -->|no| R_UNMET
    R_UNMET --> RESP
    G1 -->|no| R_OOD
    HITS -->|no| R_OOC
    ANS -->|no| R_OOC

    ERR["<b>Error response</b> {error: code, message, request_id}<br/>422 invalid_request<br/>429 rate_limited<br/>502 upstream_invalid: bad output twice<br/>503 upstream_unavailable: timeout, connection,<br/>429, 5xx or 12 s deadline exceeded<br/>500 internal_error: unexpected exception"]
    SVC -.->|"502 / 503 / 500"| ERR

    OK["200 AskResponse<br/>answer, citations 1-3,<br/>meta: request_id, model,<br/>corpus_version, latency_ms"]
    MAP --> OK
    R_OOD --> RESP
    R_SAFE --> RESP
    R_OOC --> RESP
    RESP["200 AskResponse<br/>answer = null, refusal_reason,<br/>refusal_message, meta"]

    OK --> LOG
    RESP --> LOG
    ERR --> LOG
    LOG["Emit one JSON log line for every response,<br/>errors included: status, error,<br/>extraction, eligible count, rejection<br/>reasons, top_k scores, cited_ids,<br/>token usage per stage, stage timings<br/>(whatever the request reached)"]
    LOG --> U

    INDEX[("In-memory BM25 index<br/>built at startup from<br/>data/recipes.jsonl")]
    MANI[("data/manifest.json<br/>titles, URLs, corpus_version")]
    INDEX -.-> SAFE
    INDEX -.-> BM
    MANI -.-> MAP

    classDef code fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef llm fill:#ffedd5,stroke:#ea580c,color:#7c2d12,stroke-width:2px
    classDef data fill:#f3f4f6,stroke:#6b7280,color:#111827
    classDef err fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef refuse fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef ok fill:#dcfce7,stroke:#16a34a,color:#14532d

    class RID,RL,VAL,CC,G1,G2,ELIG,ANYELIG,BM,HITS,CTX,CHK,ANS,MAP,LOG code
    class EX,GEN llm
    class EXOUT,INDEX,MANI data
    class ERR err
    class R_OOD,SAFE,R_SAFE,R_OOC,R_UNMET,RESP refuse
    class OK ok
    class ELIG planned
```

### Who decides what

| Step | Done by | What it decides |
|---|---|---|
| Rate limit, validation, concurrency | code | whether the request is served at all |
| Extract | LLM #1 | reads the question: is it about cooking, is it a safety question, which constraints it states. It never answers. |
| Domain and safety gates | code | `out_of_domain` and `safety` refusals, from the extracted flags |
| Eligibility | code | which recipes satisfy time, diet and ingredient constraints, over the whole corpus before ranking; none → `out_of_corpus` naming the constraints |
| Retrieval | code | which 3 recipes the model may see (BM25 over whole recipes, ADR 001) |
| Generate | LLM #2 | phrases an answer from those 3 recipes and names its sources, or says they cannot answer |
| Citation check | code | rejects any source ID outside the given 3; retries once, then 502 |
| Citations | code | titles and URLs come from the manifest, never from the model (I3) |

The model sees only the question (call 1) and three rendered recipes plus
the question (call 2). User text and recipe text are wrapped in tags with
angle brackets escaped, so neither can open or close a prompt tag.

## 2. Offline: building the corpus

Runs before deployment, never on a request. Its output is committed and
loaded into memory at startup.

```mermaid
flowchart LR
    WB[("Wikibooks Cookbook<br/>MediaWiki API")]
    SEL["scripts/select_corpus.py<br/>pick 40-60 pages,<br/>pin revid + sha256,<br/>write raw wikitext"]
    CS[("data/corpus_selection.json")]
    RAW[("data/raw/<br/>pinned wikitext")]
    ING["scripts/ingest.py<br/>parse wikitext: ingredients, steps,<br/>sections, time, servings, diet<br/>each fact: explicit / derived / unknown"]
    REC[("data/recipes.jsonl")]
    MAN[("data/manifest.json<br/>corpus_version")]
    APP["App startup<br/>BM25 index + citation map"]

    WB --> SEL
    SEL --> CS --> ING
    SEL --> RAW --> ING
    WB -.->|"ingest.py fallback: fetch a missing<br/>raw file by its pinned revid"| RAW
    ING --> REC --> APP
    ING --> MAN --> APP

    classDef code fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef data fill:#f3f4f6,stroke:#6b7280,color:#111827
    class SEL,ING,APP code
    class WB,CS,RAW,REC,MAN data
```

No LLM is involved in ingestion: every metadata value is either stated on
the page or derived by a documented rule, with the evidence kept.
