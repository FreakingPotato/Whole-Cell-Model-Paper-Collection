# Graph Report - Whole-Cell Model Paper Collection  (2026-04-08)

## Corpus Check
- 43 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 46 nodes · 327 edges · 3 communities detected
- Extraction: 76% EXTRACTED · 24% INFERRED · 0% AMBIGUOUS · INFERRED: 77 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `Bringing the genetically minimal cell to life on a computer in 4D` - 43 edges
2. `Mechanistic models` - 36 edges
3. `Dynamics of chromosome organization in a minimal bacterial cell` - 31 edges
4. `Essential metabolism for a minimal cell` - 26 edges
5. `Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps` - 26 edges
6. `Fundamental behaviors emerge from simulations of a living minimal cell` - 23 edges
7. `Design and synthesis of a minimal bacterial genome` - 23 edges
8. `Molecular dynamics simulation of an entire cell` - 19 edges
9. `A Whole-Cell Computational Model Predicts Phenotype from Genotype` - 18 edges
10. `Genetic requirements for cell division in a genomically minimal cell` - 18 edges

## Surprising Connections (you probably didn't know these)
- `Fundamental behaviors emerge from simulations of a living minimal cell` --semantically_similar_to--> `How to build the virtual cell with artificial intelligence: Priorities and opportunities`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-003.md → graphify_corpus/papers/WCM-006.md
- `Metabolism, cell growth and the bacterial cell cycle` --semantically_similar_to--> `Simultaneous cross-evaluation of heterogeneous E. coli datasets via mechanistic simulation`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-004.md → graphify_corpus/papers/WCM-007.md
- `Integrating cellular and molecular structures and dynamics into whole-cell models` --semantically_similar_to--> `Lattice microbes: High‐performance stochastic simulation method for the reaction‐diffusion master equation`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-005.md → graphify_corpus/papers/WCM-025.md
- `Integrating cellular and molecular structures and dynamics into whole-cell models` --semantically_similar_to--> `How to build the virtual cell with artificial intelligence: Priorities and opportunities`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-005.md → graphify_corpus/papers/WCM-006.md
- `How to build the virtual cell with artificial intelligence: Priorities and opportunities` --semantically_similar_to--> `Design and synthesis of a minimal bacterial genome`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-006.md → graphify_corpus/papers/WCM-012.md
- `Simultaneous cross-evaluation of heterogeneous E. coli datasets via mechanistic simulation` --semantically_similar_to--> `Essential metabolism for a minimal cell`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-007.md → graphify_corpus/papers/WCM-013.md
- `Cross-evaluation of E. coli’s operon structures via a whole-cell model suggests alternative cellular benefits for low- versus high-expressing operons` --semantically_similar_to--> `Essential metabolism for a minimal cell`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-008.md → graphify_corpus/papers/WCM-013.md
- `Cross-evaluation of E. coli’s operon structures via a whole-cell model suggests alternative cellular benefits for low- versus high-expressing operons` --semantically_similar_to--> `Design and synthesis of a minimal bacterial genome`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-008.md → graphify_corpus/papers/WCM-012.md

## Hyperedges (group relationships)
- **Mechanistic models** — WCM-001, WCM-002, WCM-003, WCM-004, WCM-009, WCM-010, WCM-011, WCM-012, WCM-013, WCM-014, WCM-016, WCM-017, WCM-018, WCM-019, WCM-020, WCM-021, WCM-022, WCM-024, WCM-025, WCM-026, WCM-027, WCM-029, WCM-030, WCM-031, WCM-032, WCM-033, WCM-034, WCM-035, WCM-036, WCM-037, WCM-038, WCM-039, WCM-040, WCM-041, WCM-042, WCM-043 [EXTRACTED 1.00]
- **Machine Learning Model** — WCM-006 [EXTRACTED 1.00]
- **Hybrid architectures** — WCM-005, WCM-007, WCM-008, WCM-015, WCM-023, WCM-028 [EXTRACTED 1.00]

## Communities

### Community 0 - "Mechanistic models"
Cohesion: 0.36
Nodes (37): Mechanistic models, Bringing the genetically minimal cell to life on a computer in 4D, A Whole-Cell Computational Model Predicts Phenotype from Genotype, Fundamental behaviors emerge from simulations of a living minimal cell, Metabolism, cell growth and the bacterial cell cycle, Toward a Whole-Cell Model of Ribosome Biogenesis: Kinetic Modeling of SSU Assembly, Ribosome biogenesis in replicating cells: Integration of experiment and theory, Creation of a Bacterial Cell Controlled by a Chemically Synthesized Genome (+29 more)

### Community 1 - "Machine Learning Model"
Cohesion: 1.0
Nodes (2): Machine Learning Model, How to build the virtual cell with artificial intelligence: Priorities and opportunities

### Community 2 - "Hybrid architectures"
Cohesion: 0.67
Nodes (7): Hybrid architectures, Integrating cellular and molecular structures and dynamics into whole-cell models, Simultaneous cross-evaluation of heterogeneous E. coli datasets via mechanistic simulation, Cross-evaluation of E. coli’s operon structures via a whole-cell model suggests alternative cellular benefits for low- versus high-expressing operons, Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps, Integrative modeling of JCVI-Syn3A nucleoids with a modular approach, Building Structural Models of a Whole Mycoplasma Cell

## Knowledge Gaps
- **1 isolated node(s):** `Machine Learning Model`
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Machine Learning Model`** (2 nodes): `Machine Learning Model`, `How to build the virtual cell with artificial intelligence: Priorities and opportunities`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Bringing the genetically minimal cell to life on a computer in 4D` connect `Mechanistic models` to `Machine Learning Model`, `Hybrid architectures`?**
  _High betweenness centrality (0.222) - this node is a cross-community bridge._
- **Why does `Dynamics of chromosome organization in a minimal bacterial cell` connect `Mechanistic models` to `Hybrid architectures`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `Dynamics of chromosome organization in a minimal bacterial cell` (e.g. with `Ribosome biogenesis in replicating cells: Integration of experiment and theory` and `Cell Reproduction and Morphological Changes in Mycoplasma capricolum`) actually correct?**
  _`Dynamics of chromosome organization in a minimal bacterial cell` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Essential metabolism for a minimal cell` (e.g. with `Metabolism, cell growth and the bacterial cell cycle` and `Lattice microbes: High‐performance stochastic simulation method for the reaction‐diffusion master equation`) actually correct?**
  _`Essential metabolism for a minimal cell` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps` (e.g. with `Ribosome biogenesis in replicating cells: Integration of experiment and theory` and `Spatial organization of the flow of genetic information in bacteria`) actually correct?**
  _`Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Machine Learning Model` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
