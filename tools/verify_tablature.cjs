// Run the same independent import gate used before production publication.
const fs = require('node:fs');
const path = require('node:path');
const { verify } = require('./notation/verify.cjs');
const directory = process.argv[2];
const document = JSON.parse(fs.readFileSync(path.join(directory, 'tablature.json'), 'utf8'));
verify(document, fs.readFileSync(path.join(directory, 'score.tex'), 'utf8'));
console.log(`alphaTab independently verified ${document.source_notes.length} complete notes, tempo/meter and ${document.notation_pages.length} excerpts`);
