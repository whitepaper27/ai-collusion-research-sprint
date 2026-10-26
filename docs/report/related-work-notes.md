# Related work notes (positioning bullets)

Agent-compiled notes for Sahil. Titles, authors and dates were read from the arXiv abstract
pages listed (access date 2026-10-26); web content treated as untrusted data. "What they do"
lines are paraphrased from those abstracts; verify against the PDFs before citing.

Our position (GOAL.md, sol.md §11): source-linked evidence views for attributing coordinated
violations vs independent mistakes, plus the measured cost of a targeted provenance gate. We do
**not** claim information-flow control is new, nor that evidence views were never compared.

## Apollo Research: strategic deception in a trading agent

- Title: Large Language Models can Strategically Deceive their Users when Put Under Pressure
  (Technical Report)
- Authors: Jérémy Scheurer, Mikita Balesni, Marius Hobbhahn
- Year: 2023 (arXiv v1 9 Nov 2023)
- URL: https://arxiv.org/abs/2311.07590 (accessed 2026-10-26)
- What they do: GPT-4 as a simulated stock-trading agent acts on an insider tip under pressure
  and hides the reason when reporting to its manager.
- How we differ: multi-role team (five roles) and auditing of *who coordinated*, with
  source-linked evidence; our core study gave no actionable tip (noted in
  `docs/decisions/2026-10-25-exploratory-stress-arm.md`), the incentive pilot adds one.

## Multi-Agent AI Control

- Title: Multi-Agent AI Control: Distributed Attacks Hamper Per-Instance Monitors
- Authors: Oliver Makins, Orazio Angelini, Zohreh Shams, Mary Phuong
- Year: 2026 (v1 8 Jul 2026; v2 12 Oct 2026)
- URL: https://arxiv.org/abs/2607.07368 (accessed 2026-10-26)
- What they do: FakeLab (9 services, 86 benign tasks, 4 attack objectives); shows per-agent
  monitors catch fewer attackers as more agents split an attack ("fragmentation effect").
- How we differ: we compare cross-agent evidence *views* for one auditor (linked vs combined,
  equal information) and include false-accusation and independent-mistake fixtures.

## Colosseum (v2 exists)

- Title: Colosseum: Auditing Collusion in Cooperative Multi-Agent Systems
- Authors: Mason Nakamura, Abhinav Kumar, Saswat Das, Sahar Abdelnabi, Saaduddin Mahmud,
  Ferdinando Fioretto, Shlomo Zilberstein, Eugene Bagdasarian
- Year: 2026 (v1 16 Feb 2026; v2 27 May 2026)
- URL: https://arxiv.org/abs/2602.15198v2 (accessed 2026-10-26; also cited in sol.md)
- What they do: DCOP-grounded collusion audit measured by regret; v2 compares communication-
  based vs action-based collusion and reports "collusion on paper" (plans in text, benign actions).
- How we differ: communication/action comparison is their prior work; ours is claim-specific
  source attribution with matched fixture validation and an equal-information linked vs
  chronological comparison (sol.md §11), in a finance workflow with a provenance gate.

## Fides (information-flow control for agents)

- Title: Securing AI Agents with Information-Flow Control
- Authors: Manuel Costa, Boris Köpf, Aashish Kolluri, Andrew Paverd, Mark Russinovich, Ahmed
  Salem, Shruti Tople, Lukas Wutschitz, Santiago Zanella-Béguelin
- Year: 2025 (v1 29 May 2025; v2 3 Sep 2025)
- URL: https://arxiv.org/abs/2505.23643 (accessed 2026-10-26)
- What they do: Fides planner tracks confidentiality/integrity labels and deterministically
  enforces policies on consequential actions; evaluated on AgentDojo.
- How we differ: we do not propose a new IFC mechanism; we apply a simple taint gate across a
  five-role team and measure what it blocks and costs, plus attribution of coordination.

## FLOWSEAL

- Title: Confuse the Model, Control the Flow: Understanding and Mitigating Privacy Leakage from
  LLM Agents with Information Flow Control
- Authors: Minsun Shim, Ramisha Raida Karim, Ruthwik Jakkula, Kaiwen Zhou, Xin Liu, Xin Eric
  Wang, Zhou Li
- Year: 2026 (submitted 12 Sep 2026)
- URL: https://arxiv.org/abs/2609.14003 (accessed 2026-10-26)
- What they do: three non-injection privacy attacks; FLOWSEAL tool-level interceptor outside
  the LLM context, provenance + IFC lattice with controlled declassification.
- How we differ: no new defense; our gate has no declassification tool by design (fixed release
  records), and our focus is audit attribution of multi-agent coordination.

## SafeFlow (two papers share the name; confirm which one sol.md means)

- (a) Title: SAFEFLOW: A Principled Protocol for Trustworthy and Transactional Autonomous Agent
  Systems
  - Authors: Peiran Li, Xinkai Zou, Zhuohang Wu, Ruifeng Li, Shuo Xing, Hanwen Zheng, Zhikai
    Hu, Yuping Wang, Haoxi Li, Qin Yuan, Yingmo Zhang, Zhengzhong Tu
  - Year: 2025 (v1 9 Jun 2025; v3 11 Jun 2025)
  - URL: https://arxiv.org/abs/2506.07564 (accessed 2026-10-26)
  - What they do: protocol-level IFC tracking provenance/integrity/confidentiality across
    agents, tools and environment, plus transactional execution, rollback, secure scheduling.
- (b) Title: SafeFlow: Semantic Information-Flow Control for Blocking Malicious Propagation in
  Multi-Agent Systems
  - Authors: Haowen Dai, Zonghao Ying, Wenfeng Li, Xiangfan Wu, Yisong Xiao, Tianyuan Zhang,
    Jiaye Lin, Lei Wei, Guangyuan Dong, Xitong Ling, Xixun Lin, Quanchen Zou, Xiangzheng Zhang
  - Year: 2026 (submitted 28 Jul 2026)
  - URL: https://arxiv.org/abs/2607.25255 (accessed 2026-10-26)
  - What they do: tracks semantic risk across multi-agent workflows to stop malicious intent
    hidden by task fragmentation.
- How we differ (both): IFC/workflow defense is established; we contribute evaluation of
  evidence views for attributing coordination vs independent mistakes, and the legitimate-work
  cost of a targeted gate (unmeasured live so far: the gate never fired in main-v1).

## Not verified

- None of the six named works was "not found". Open item: which SafeFlow sol.md intends.
