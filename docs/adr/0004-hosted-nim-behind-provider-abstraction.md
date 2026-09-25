<!-- 
  Architecture Decision Record: Hosted NIM Behind Provider Abstraction
  What: Documents the architectural decision to use hosted NVIDIA NIM for inference
        isolated behind a strict provider protocol (InferenceProvider).
  Standards: Follows MADR template and HLD §12.2, §16.
-->

# ADR-0004: Hosted NIM Behind Provider Abstraction

| Field    | Value                                                              |
|----------|--------------------------------------------------------------------|
| Status   | Accepted                                                           |
| Date     | 2026-09-25                                                         |
| Decides  | Use hosted NVIDIA NIM behind an abstract InferenceProvider interface |

## Context

VEYDUS requires embeddings for semantic retrieval (`nvidia/llama-nemotron-embed-1b-v2`) and large language models for query rewriting and response generation (`meta/llama-3.1-70b-instruct`). Self-hosting dedicated GPU instances (e.g., via vLLM on GCP Compute Engine) introduces prohibitive fixed infrastructure costs under the zero-budget MVP constraints and adds infrastructure maintenance overhead before product validation. Conversely, tightly coupling application code to vendor-specific SDKs risks vendor lock-in and impedes future private-cloud or on-prem deployments.

## Decision

Utilize **hosted NVIDIA NIM endpoints** for model inference during the MVP, isolated strictly behind an abstract **`InferenceProvider` Protocol** (`providers/base.py`).

## Rationale

1. **Zero Fixed Infrastructure Cost**: Hosted NIM API tiers provide immediate access to enterprise-grade embedding and instruction models with zero idle GPU spend.
2. **Strict Inversion of Control**: The application (ingestion workers, query rewriters, generation pipelines) interacts solely with the `InferenceProvider` interface defining `embed()` and `generate_stream()` methods.
3. **Seamless Portability**: Transitioning from hosted NIM to self-hosted NIM on private GPUs, vLLM, or alternative models (Ollama, Vertex AI, Anthropic) requires only implementing a new adapter class fulfilling the protocol and altering the `INFERENCE_PROVIDER` configuration setting.
4. **Normalized Matryoshka Embeddings**: The provider abstraction handles model-specific nuances (such as 2048 -> 768 Matryoshka dimension truncation followed by L2 normalization per HLD §12.2) consistently in one place.

## Consequences

- All LLM calls and embedding requests must pass through `InferenceProvider`. Direct HTTP calls to AI APIs from RAG pipelines are prohibited.
- Network latency and third-party rate limits (HTTP 429) must be managed with exponential backoff and jitter in the provider adapter.
