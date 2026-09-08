---
arete: true
deck: Corpus::Deps
model: Basic
cards:
- id: arete_corpus_dep_parent
  Front: What must you know first?
  Back: The prerequisite.
- id: arete_corpus_dep_child
  Front: What comes second?
  Back: The dependent.
  deps:
    requires:
    - arete_corpus_dep_parent
    related: []
---

Body.
