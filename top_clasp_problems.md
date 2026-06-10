In industrial tech, there are four major "Hard Parts" that could stop us if we don't design around them from day one.

Hard Part 1: The "Spurious Correlation" Problem (Math/Logic)
In a chemical plant, thousands of sensors change values simultaneously. If a cooling pump starts vibrating at the exact same time the operator changes the setpoint on a feed valve, a simple statistical algorithm might assume the valve change caused the vibration.

Why it stops others: Naive causal AI produces "dirty graphs" full of false links. If Clasp gives operators three false alarms or incorrect root causes, they will turn it off and never turn it back on. Trust is incredibly hard to build and takes one mistake to destroy.
Our solution: We don't just rely on raw statistics. We prune the graph using domain heuristics (e.g., fluid cannot travel faster than speed of sound, a sensor downstream cannot cause an event upstream). We also use LLM agents to cross-reference statistical correlations with known chemical/physical laws before writing an edge to the graph.
Hard Part 2: "OPC-UA Hell" (Data Engineering)
Factories are a museum of industrial history. A single plant might run brand-new Siemens PLCs alongside 30-year-old Allen-Bradley controllers communicating over ancient serial cables.

Why it stops others: Getting the data out of these machines and clean enough to analyze is incredibly tedious. Furthermore, no two plants name their variables the same way. One plant calls reactor temperature TIC_101.PV, another calls it RXT_TEMP_VAL.
Our solution: This is why we focus heavily on OPC-UA (the modern unified standard) and use LLMs to automate the data cleaning. We can build a specialized schema-mapping agent whose sole job is to ingest their raw tag lists and automatically map them to standard schemas (like ISA-95) using semantic reasoning.
Hard Part 3: Severe Risk Aversion (Sales/Business)
If a web application crashes, users reload the page. If an industrial application crashes or makes a bad decision, machines break, plants lose millions, or people get hurt. Because of this, plant managers are terrified of new software. They live by the motto: "If it isn't broken, don't touch it."

Why it stops others: Long sales cycles (12 to 18 months) that drain a startup's cash before they sign their first contract.
Our solution: We design Clasp as a purely passive (read-only) system to begin with. Clasp only listens to the OPC-UA stream and displays analysis. It has zero capability to send commands back to the machines (actuate) in the beginning. This eliminates the safety risk for the plant manager, dropping the barrier to entry to almost zero. We only introduce the "Optimizer" actuation leases once the system has proved its accuracy for 6+ months.
Hard Part 4: LLM Hallucinations in High-Stakes Scenarios (AI Reliability)
If a chatbot hallucinates a fact, it's annoying. If Clasp's RootCause Agent hallucinates that a safety valve is open when it is actually closed, it could lead to a catastrophic operator decision.

Why it stops others: Traditional RAG (Retrieval-Augmented Generation) is too unpredictable for critical infrastructure.
Our solution: We treat the LLM as a translator and synthesizer, not the source of truth. The actual graph traversal, time-series calculations, and safety boundary checks are done by deterministic Python code (our Silex backend). The LLM is only called to write the final explanation for the human operator (e.g., "Based on the causal graph, the temperature spike in Sensor T-10 was preceded by a 15% drop in coolant flow. Recommend checking Pump P-2.").
If we keep the LLM away from direct control and use it strictly to explain deterministic data, we bypass the hallucination risk entirely.