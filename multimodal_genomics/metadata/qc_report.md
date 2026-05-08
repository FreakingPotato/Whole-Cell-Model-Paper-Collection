# Multimodal Genomics — Evidence Screenshot QC

- checked_at: 2026-05-08T09:22:46Z
- checker: agent-QC
- evidence points (with screenshots): 46
- total screenshots: 194

## Summary

| verdict | count |
|---|---|
| ok | 182 |
| reference | 0 |
| header | 1 |
| fragment | 1 |
| off_topic | 9 |
| borderline | 1 |

**ok rate: 93.8%**

## Evidence points needing remediation

### evidence-applications-drug-discovery-MMG005

- **idx 5** (page 4, claim _Sequence-aware drug discovery_) — `off_topic`: Body sentence about BioNeMo Framework GPU training throughput / scaling — not directly about sequence-aware drug discovery.
  - sentence: The BioNeMo Framework achieved a training speed at 256 GPUs that was 96.9% of the extrapolated single-node throughput corresponding to a 60% MFU and a total time to train over 1 trillion tokens of just 4.2 days.

### evidence-applications-regulatory-annotation-MMG007

- **idx 3** (page 10, claim _Regulatory-element annotation_) — `header`: Recorded text '6 The identified binding motifs by iDeep.' is a Figure 6 caption header (renderer dropped 'Fig.' prefix), not body text.
  - sentence: 6 The identified binding motifs by iDeep.

### evidence-gfm-long-context-MMG005

- **idx 1** (page 1, claim _Long-context architecture_) — `off_topic`: Survey scope statement (FMs in bioinformatics broadly) — does not specifically support long-context-architecture claim.
  - sentence: The primary goal of this survey is to conduct a general investigation and summary of FMs in bioinformatics, tracing their evolutionary trajectory, current research landscape, and methodological frameworks.
- **idx 4** (page 12, claim _Long-context architecture_) — `off_topic`: Body sentence about FMs in biological structure construction — focuses on structure prediction, not long-context architectures.
  - sentence: FMs in biological structure construction greatly surpass the limits of conventional structure prediction methods.

### evidence-multimodal-instruction-tuning-MMG006

- **idx 2** (page 2, claim _Instruction tuning with biological context_) — `borderline`: Body sentence describing Galactica's training corpus; relevant to scientific LLMs but does not directly assert instruction tuning behavior.
  - sentence: Galactica is trained on a large and curated corpus of humanity’s scientiﬁc knowledge.
- **idx 5** (page 29, claim _Instruction tuning with biological context_) — `off_topic`: Body sentence about Galactica's reduced bias from scientific corpus — about safety/bias, not instruction tuning capability.
  - sentence: Galactica is trained on a scientiﬁc corpus where the incidence rate for stereotypes and discriminatory text is likely to be lower.

### evidence-multimodal-shared-embeddings-MMG004

- **idx 1** (page 4, claim _Shared embedding spaces_) — `off_topic`: Survey scope statement about chemical/biological languages — not specifically about shared embedding spaces between text and biological sequences.
  - sentence: This survey is confined within specific boundaries.
- **idx 2** (page 17, claim _Shared embedding spaces_) — `off_topic`: Bullet describing SciInstruct, a scientific instruction tuning dataset — relevant to instruction tuning, not shared embedding spaces.
  - sentence: • SciInstruct [395] is a comprehensive scientific instruction tuning dataset.
- **idx 5** (page 35, claim _Shared embedding spaces_) — `off_topic`: Body sentence about protein 3D structure integration efforts — not about text-sequence shared embedding spaces.
  - sentence: There are some recent efforts in this direction.

### evidence-multimodal-shared-embeddings-MMG005

- **idx 4** (page 5, claim _Shared embedding spaces_) — `off_topic`: Figure 3 caption sentence about retrieving experimental evidence from public databases — not about shared text-sequence embedding spaces.
  - sentence: Experimental evidence is retrieved from several public databases.

### evidence-multimodal-text-conditioned-generation-MMG001

- **idx 4** (page 7, claim _Text-conditioned sequence generation_) — `fragment`: Recorded sentence is just 'The property prediction results are in Table 3.' — a table pointer fragment; surrounding visual highlight contains more substantive ProteinCLAP discussion but JSON sentence is fragmentary.
  - sentence: The property prediction results are in Table 3.

### evidence-multimodal-text-conditioned-generation-MMG006

- **idx 3** (page 17, claim _Text-conditioned sequence generation_) — `off_topic`: Bullet describing SciInstruct (a scientific instruction tuning dataset) — relevant to instruction tuning, not text-conditioned sequence generation.
  - sentence: • SciInstruct [395] is a comprehensive scientific instruction tuning dataset.
