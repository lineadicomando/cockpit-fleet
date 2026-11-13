# Export and import

`config.export` returns the configuration as one JSON document: credentials,
projects, inventories, trusted host keys, sets of variables, job templates
(with the names of their sets), schedules, checks,
notification channels and rules. Things refer to
each other by name, since identifiers differ between two installations. It
holds no secret: a credential is its name, its kind, its user name and its
vault identifier, and its secrets are entered again where it is imported.
Neither does it hold the files of a project that has no source elsewhere
(written in the interface, uploaded): such a project arrives empty. The
files of an inventory written in the interface (`files`: path, and content,
or data in base64 for one that is not text, with `content` its main file for
an import made before inventories were trees) and the variables of a
template are in it as they are.

`config.import` creates what the document lists and is not there yet, by
name; what exists is left as it is, so importing twice changes nothing. Each
thing goes through the command that creates it from the interface: the same
checks apply (a local project outside the allowed roots is refused, a host key
that differs from the trusted one is not replaced) and the same audit record
is left, next to the one of the import itself. One thing that cannot be
created does not stop the others, and the answer says what happened to each.
The document is not copied into the audit record. Both commands are for
administrators.
