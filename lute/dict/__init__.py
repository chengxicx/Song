"""
Local dictionary support.

Lute's reader can show a gloss for terms that have no translation yet
(the word you have never studied), and the phone app downloads the same
dictionary to work offline.  The data comes from an MDX (MDict)
dictionary the user imports on the settings page: it is converted once,
on the server, into the small two-table SQLite file that both clients
query -- see ``lute.dict.service``.
"""
