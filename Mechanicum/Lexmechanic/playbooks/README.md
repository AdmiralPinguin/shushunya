# Source Playbooks

Source playbooks describe where a research governor should look before live
search. `Lexmechanic` loads every `.json` file in this directory, and
`CorpusIngestor` also reads these files to match local primary texts by title.

Required top-level fields:

- `name`
- `topic`
- `match_terms`
- `sources`
- `search_queries`

Each source must include:

- `title`
- `type`
- `source_class`
- `expected_use`

Primary texts that cannot be fetched publicly should still be listed with an
empty `url`. The pipeline will expose them as `corpus_requirements` and ask the
operator to provide legitimate local files in `Corpus/`.

Run `python3 EyeOfTerror/doctor.py` after editing a playbook. The full gate is
`./EyeOfTerror/check-eye-mechanicum.sh`.
