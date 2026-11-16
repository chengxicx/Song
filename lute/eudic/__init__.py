"""
Two-way sync with the Eudic (欧路词典) studylist.

The OpenAPI's ``language`` parameter selects a *product bucket*
(en/fr/de/es), not the language of the words: a Japanese list is just a
category inside the ``en`` bucket.  Everything in this package works off
an explicit (bucket, category) pair, so no language is special-cased.
"""
