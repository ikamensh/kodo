# Model audit — 8 September 2026

Kodo had several stale recommendations and two retired model families in active defaults. This refresh keeps each workload's role: balanced models for everyday coding, stronger models as explicit choices, and inexpensive summarization.

| Surface | Previous setting | Current setting | Reason and source |
| --- | --- | --- | --- |
| Codex workers and default OpenAI API orchestration | GPT-5.5 | `gpt-5.6-terra` | OpenAI recommends Terra for work previously given GPT-5.5. Astra, Sol, and Luna are also offered. [Codex model guidance](https://learn.chatgpt.com/docs/models) |
| Most capable OpenAI option | Missing | `gpt-6-astra` | Available explicitly for demanding work; it is more expensive per token than Terra. [API models](https://developers.openai.com/api/docs/models/gpt-6-astra), [pricing](https://developers.openai.com/api/docs/pricing) |
| Claude API aliases | Opus 4.7, Sonnet 4.6 | `claude-opus-5`, `claude-sonnet-5` | Current Opus and Sonnet generations. Fable 5.1 is a still more expensive specialist option, usable as an explicit model. CLI `opus`/`sonnet` aliases remain managed by Claude Code. [Claude models](https://platform.claude.com/docs/en/models/overview) |
| Gemini Flash API/CLI | `gemini-3.5-flash` | `gemini-3.8-flash` | Latest stable Flash. The public API catalog does not establish an individual Gemini CLI account's access. [Gemini models](https://ai.google.dev/gemini-api/docs/models) |
| Gemini Pro CLI | `gemini-3-pro` | `gemini-3.1-pro-preview` | Correct explicit model ID; Gemini CLI documents this spelling. API Pro already used it and remains unchanged. [Gemini CLI guide](https://geminicli.com/docs/get-started/gemini-3/) |
| Summarizer | `gemini-3.1-flash-lite-preview` | `gemini-3.1-flash-lite` | The preview is shut down. Keep the cheaper stable 3.1 model; the newer 3.5 Flash-Lite is also resolvable explicitly. [Model lifecycle](https://ai.google.dev/gemini-api/docs/models), [pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| Cursor | Composer 2.5 | Composer 2.5 | Still current in the installed account's `cursor-agent models` catalog. The orchestrator menu's stale third-party choices were replaced with IDs from that catalog. |
| DeepSeek aliases | `deepseek-chat`, `deepseek-reasoner` | `deepseek-v4-flash`, `deepseek-v4-pro` | The old API IDs retired in July. Both V4 models support thinking and tools. [Current models](https://api-docs.deepseek.com/), [pricing](https://api-docs.deepseek.com/quick_start/pricing/), [release history](https://api-docs.deepseek.com/updates/) |
| xAI | `grok-4.1` | `grok-4.6` | Current documented model for coding and tool calling. [Models and pricing](https://docs.x.ai/developers/models) |
| Kimi | `kimi-k2.5` | Native CLI configured model | The previous API ID retired on August 31. Managed CLI aliases differ from API model IDs, so native login keeps the configured choice; explicit native aliases are passed to the CLI. [Kimi API models](https://platform.kimi.ai/docs/models), [Kimi Code model configuration](https://www.kimi.com/code/docs/en/kimi-code/models.html) |

## Implementation and validation

Model aliases, bare IDs, provider-qualified IDs, display names, and prices now share one registry lookup. Previously a resolved model such as `openai:gpt-5.5` lost its metadata and could report zero cost. Known older models that remain valid retain pricing metadata for explicitly pinned configurations, while new menus recommend current models. Unknown explicit model IDs remain unchanged.

OpenAI orchestration uses Responses, following [OpenAI's migration guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6). Explicit `openai-chat:` and `openai-responses:` prefixes select the endpoint and use OpenAI authentication even for custom model IDs. Both forms retain known model pricing. The older Pydantic model profile needed an explicit Astra capability to preserve encrypted reasoning across tool calls.

Tests use actual Pydantic AI and provider SDKs with intercepted HTTP. They exercise a tool call followed by a tool-result continuation, asserting the endpoint, selected model, final answer, and preservation of reasoning for Terra, Astra, Opus, Sonnet, Gemini Flash, and both DeepSeek V4 models. DeepSeek's continuation requires the modern SDK to replay `reasoning_content`; Pydantic AI 1.20 explicitly discarded it. Native xAI model construction was also checked. Registry tests assert that metadata survives alias resolution. These checks establish protocol compatibility; they do not measure model quality or establish live account entitlement.

The installed Codex catalog confirms the selected OpenAI model IDs. Cursor's live read-only model listing confirms Composer and its replacement menu choices. No paid inference benchmark was run. The installed Gemini CLI was version 0.32.1; its explicit model flag passes concrete IDs through, but updating that globally installed CLI is a separate environment action.

## Pricing limits and follow-ups

Recorded prices are standard uncached input/output USD per million tokens. They are estimates: they omit cache read/write rates, long-context premiums, external tool charges, and subscription quota accounting. Gemini 3.8 Flash's introductory $0.75/$3.75 rates end on December 31, 2026; published January rates are $1.50/$7.50. DeepSeek entries use peak rates ($0.44/$1.32 Flash, $1.32/$3.96 Pro); off-peak is half. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/).

The optional Groq, Mistral, and OpenRouter catalog entries were not individually certified against account-specific catalogs. Kiro and OpenCode continue to use their configured default model. A useful next improvement is a read-only model-access check that reports unavailable IDs and stale prices before a run; it should preserve deliberate model pins and never silently choose a different provider or price tier.
