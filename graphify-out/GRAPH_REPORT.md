# Graph Report - Whole-Cell Model Paper Collection  (2026-05-04)

## Corpus Check
- 56 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 59 nodes · 388 edges · 3 communities detected
- Extraction: 73% EXTRACTED · 27% INFERRED · 0% AMBIGUOUS · INFERRED: 105 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `Bringing the genetically minimal cell to life on a computer in 4D` - 47 edges
2. `Mechanistic models` - 41 edges
3. `Dynamics of chromosome organization in a minimal bacterial cell` - 31 edges
4. `Essential metabolism for a minimal cell` - 26 edges
5. `Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps` - 26 edges
6. `Fundamental behaviors emerge from simulations of a living minimal cell` - 24 edges
7. `A Whole-Cell Computational Model Predicts Phenotype from Genotype` - 23 edges
8. `Design and synthesis of a minimal bacterial genome` - 22 edges
9. `Molecular dynamics simulation of an entire cell` - 19 edges
10. `Integrating cellular and molecular structures and dynamics into whole-cell models` - 18 edges

## Surprising Connections (you probably didn't know these)
- `Bringing the genetically minimal cell to life on a computer in 4D` --semantically_similar_to--> `Multi-omics integration accurately predicts cellular state in unexplored conditions for Escherichia coli`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-001.md → graphify_corpus/papers/WCM-044.md
- `Bringing the genetically minimal cell to life on a computer in 4D` --semantically_similar_to--> `Predicting cellular responses to perturbation across diverse contexts with State`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-001.md → graphify_corpus/papers/WCM-051.md
- `Fundamental behaviors emerge from simulations of a living minimal cell` --semantically_similar_to--> `How to build the virtual cell with artificial intelligence: Priorities and opportunities`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-003.md → graphify_corpus/papers/WCM-006.md
- `Metabolism, cell growth and the bacterial cell cycle` --semantically_similar_to--> `A neural-mechanistic hybrid approach improving the predictive power of genome-scale metabolic models`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-004.md → graphify_corpus/papers/WCM-046.md
- `Integrating cellular and molecular structures and dynamics into whole-cell models` --semantically_similar_to--> `Lattice microbes: High‐performance stochastic simulation method for the reaction‐diffusion master equation`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-005.md → graphify_corpus/papers/WCM-025.md
- `Integrating cellular and molecular structures and dynamics into whole-cell models` --semantically_similar_to--> `scGPT: toward building a foundation model for single-cell multi-omics using generative AI`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-005.md → graphify_corpus/papers/WCM-050.md
- `How to build the virtual cell with artificial intelligence: Priorities and opportunities` --semantically_similar_to--> `Design and synthesis of a minimal bacterial genome`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-006.md → graphify_corpus/papers/WCM-012.md
- `Cross-evaluation of E. coli’s operon structures via a whole-cell model suggests alternative cellular benefits for low- versus high-expressing operons` --semantically_similar_to--> `scGPT: toward building a foundation model for single-cell multi-omics using generative AI`  [INFERRED] [semantically similar]
  graphify_corpus/papers/WCM-008.md → graphify_corpus/papers/WCM-050.md

## Hyperedges (group relationships)
- **Mechanistic models** — WCM-001, WCM-002, WCM-003, WCM-004, WCM-009, WCM-010, WCM-011, WCM-012, WCM-013, WCM-014, WCM-016, WCM-017, WCM-018, WCM-019, WCM-020, WCM-021, WCM-022, WCM-024, WCM-025, WCM-026, WCM-027, WCM-029, WCM-030, WCM-031, WCM-032, WCM-033, WCM-034, WCM-035, WCM-036, WCM-037, WCM-038, WCM-039, WCM-040, WCM-041, WCM-042, WCM-043, WCM-048, WCM-052, WCM-054, WCM-055, WCM-056 [EXTRACTED 1.00]
- **Machine Learning Model** — WCM-006, WCM-049, WCM-050, WCM-051 [EXTRACTED 1.00]
- **Hybrid architectures** — WCM-005, WCM-007, WCM-008, WCM-015, WCM-023, WCM-028, WCM-044, WCM-045, WCM-046, WCM-047, WCM-053 [EXTRACTED 1.00]

## Communities

### Community 0 - "Mechanistic models"
Cohesion: 0.3
Nodes (42): Mechanistic models, Bringing the genetically minimal cell to life on a computer in 4D, A Whole-Cell Computational Model Predicts Phenotype from Genotype, Fundamental behaviors emerge from simulations of a living minimal cell, Metabolism, cell growth and the bacterial cell cycle, Toward a Whole-Cell Model of Ribosome Biogenesis: Kinetic Modeling of SSU Assembly, Ribosome biogenesis in replicating cells: Integration of experiment and theory, Creation of a Bacterial Cell Controlled by a Chemically Synthesized Genome (+34 more)

### Community 1 - "Machine Learning Model"
Cohesion: 1.0
Nodes (5): Machine Learning Model, How to build the virtual cell with artificial intelligence: Priorities and opportunities, A Cross-Species Generative Cell Atlas Across 1.5 Billion Years of Evolution: The TranscriptFormer Single-cell Model, scGPT: toward building a foundation model for single-cell multi-omics using generative AI, Predicting cellular responses to perturbation across diverse contexts with State

### Community 2 - "Hybrid architectures"
Cohesion: 0.45
Nodes (12): Hybrid architectures, Integrating cellular and molecular structures and dynamics into whole-cell models, Simultaneous cross-evaluation of heterogeneous E. coli datasets via mechanistic simulation, Cross-evaluation of E. coli’s operon structures via a whole-cell model suggests alternative cellular benefits for low- versus high-expressing operons, Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps, Integrative modeling of JCVI-Syn3A nucleoids with a modular approach, Building Structural Models of a Whole Mycoplasma Cell, Multi-omics integration accurately predicts cellular state in unexplored conditions for Escherichia coli (+4 more)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Bringing the genetically minimal cell to life on a computer in 4D` connect `Mechanistic models` to `Machine Learning Model`, `Hybrid architectures`?**
  _High betweenness centrality (0.226) - this node is a cross-community bridge._
- **Why does `Essential metabolism for a minimal cell` connect `Mechanistic models` to `Hybrid architectures`?**
  _High betweenness centrality (0.057) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `Bringing the genetically minimal cell to life on a computer in 4D` (e.g. with `Comprehensive understanding of <i>Saccharomyces cerevisiae</i> phenotypes with whole‐cell model WM_S288C` and `An expanded whole-cell model of E. coli links cellular physiology with mechanisms of growth rate control`) actually correct?**
  _`Bringing the genetically minimal cell to life on a computer in 4D` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Dynamics of chromosome organization in a minimal bacterial cell` (e.g. with `Ribosome biogenesis in replicating cells: Integration of experiment and theory` and `Comprehensive understanding of <i>Saccharomyces cerevisiae</i> phenotypes with whole‐cell model WM_S288C`) actually correct?**
  _`Dynamics of chromosome organization in a minimal bacterial cell` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Essential metabolism for a minimal cell` (e.g. with `Overflow metabolism originates from growth optimization and cell heterogeneity` and `Metabolism, cell growth and the bacterial cell cycle`) actually correct?**
  _`Essential metabolism for a minimal cell` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps` (e.g. with `Ribosome biogenesis in replicating cells: Integration of experiment and theory` and `Multi-omics integration accurately predicts cellular state in unexplored conditions for Escherichia coli`) actually correct?**
  _`Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps` has 4 INFERRED edges - model-reasoned connections that need verification._
