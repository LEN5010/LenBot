# Ambient Execution Scope Enforcement at the SQL Layer

Privacy and social boundary separation cannot rely on LLM prompts. We decided that retrieval tools receive an ambient `ExecutionScope` injected by the runtime, which unconditionally injects `AND scope IN (...)` constraints directly into underlying SQL queries. The LLM has no parameters or authority to widen its visibility scope beyond what the runtime explicitly grants for that episode.
