/*
 * Lift named functions out of a browser script so node can test them.
 *
 * The CCT apps keep their front ends as one large module loaded with
 * <script type="module">, which means nothing in them is reachable from
 * node: no exports, and the module scope is closed. Rather than fork the
 * logic into a testable copy -- the very drift these tests exist to
 * catch -- a test names the functions it wants and gets their source
 * text, which it then evaluates with whatever stubs it chooses.
 *
 * This is the single implementation of that trick. It was five
 * copy-pasted ones (four node harnesses in PCB Importer plus a Python
 * re-implementation for the cross-language parity test), identical
 * modulo whitespace, which meant a weakness in the matcher was a
 * weakness in all of them and a fix reached none of the others.
 *
 * KNOWN LIMIT: only `function name(...)` declarations. A `const name =
 * (...) => ...` is not found, and asking for one raises rather than
 * silently returning nothing. Destructured parameters ARE handled --
 * see `_bodyStart`, which is where that stopped being true once.
 */
'use strict';

// Is the match at `idx` a real declaration, or just the name written
// inside a comment? Declarations start their own line; a mention in
// prose does not. Checked because these files carry long comment blocks
// that name the functions below them, and `indexOf` finds the prose
// first -- returning a fragment of a comment as if it were code.
function _isDeclaration(src, idx) {
  let i = idx - 1;
  while (i >= 0 && (src[i] === ' ' || src[i] === '\t')) i--;
  if (i < 0 || src[i] === '\n') return true;
  // `export function f()` / `async function f()` are still declarations.
  const before = src.slice(Math.max(0, i - 12), idx);
  return /\b(export|async|export\s+default)\s+$/.test(before);
}

// Where a function's BODY begins -- past its parameter list.
//
// Matching braces from the first `{` after the name is wrong the moment
// a parameter is destructured: `function f({ reselect = true } = {})`
// puts a brace in the SIGNATURE, and the match closed on that one and
// returned the signature alone as if it were the whole function. The
// caller then evaluated a fragment, and the SyntaxError node reported
// pointed at the test rather than at this line.
//
// Parens are counted rather than sought, because a default value can
// contain them: `function f(a = g(1))`.
function _bodyStart(src, start, name, label) {
  let depth = 0;
  for (let j = src.indexOf('(', start); j < src.length; j++) {
    if (src[j] === '(') depth++;
    else if (src[j] === ')' && --depth === 0) {
      const brace = src.indexOf('{', j);
      if (brace < 0) throw new Error(`no body for ${name} (${label})`);
      return brace;
    }
  }
  throw new Error(`unbalanced parentheses in ${name} (${label})`);
}

/** Source text of `name`, from its `function` keyword to its closing brace. */
function extractFunction(src, name, label = 'the source') {
  const needle = `function ${name}(`;
  let start = -1;
  for (let at = src.indexOf(needle); at >= 0; at = src.indexOf(needle, at + 1)) {
    if (_isDeclaration(src, at)) { start = at; break; }
  }
  if (start < 0) throw new Error(`${name} not found in ${label}`);

  let depth = 0;
  for (let j = _bodyStart(src, start, name, label); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error(`unbalanced braces in ${name} (${label})`);
}

/** The named functions' source, concatenated, in the order given. */
function extractFunctions(src, names, label = 'the source') {
  return names.map((n) => extractFunction(src, n, label)).join('\n\n');
}

// Bracket-match forward from the table's OWN opening bracket, whichever
// it is: a lookup table is as often an array as an object (`['front',
// 'right', 'back', 'left']`), and counting only braces walked straight
// past an array's `[`, ran on to the first `{` anywhere later in the
// file and returned somebody else's object.
const _CLOSER = { '{': '}', '[': ']' };
function _closingBrace(src, from, what, label) {
  const at = _tableOpen(src, from);
  const open = src[at];
  const shut = _CLOSER[open];
  let depth = 0;
  for (let j = at; j < src.length; j++) {
    if (src[j] === open) depth++;
    else if (src[j] === shut && --depth === 0) return j;
  }
  throw new Error(`unbalanced ${open}${shut} in ${what} (${label})`);
}

/** The table's opening bracket: the first `{` or `[` at or after `from`. */
function _tableOpen(src, from) {
  const brace = src.indexOf('{', from);
  const square = src.indexOf('[', from);
  if (brace < 0) return square;
  if (square < 0) return brace;
  return Math.min(brace, square);
}

function _objectStart(src, name, label) {
  for (const open of ['{', '[']) {
    const needle = `const ${name} = ${open}`;
    for (let at = src.indexOf(needle); at >= 0;
         at = src.indexOf(needle, at + 1)) {
      if (_isDeclaration(src, at)) return at;
    }
  }
  throw new Error(`${name} not found in ${label}`);
}

/**
 * A top-level `const NAME = {...};` or `const NAME = [...];`, whole.
 *
 * For the lookup tables the functions read. Restating one in a test
 * would only prove a copy matches a copy -- and one of these (the corner
 * neighbour table) had three wrong entries, which a restated copy would
 * have agreed with perfectly.
 */
function extractObject(src, name, label = 'the source') {
  const start = _objectStart(src, name, label);
  const close = _closingBrace(src, start, name, label);
  const semi = src.indexOf(';', close);
  return src.slice(start, (semi < 0 ? close : semi) + 1);
}

/** The same table, as a bare literal -- for `return {...};`. */
function extractObjectLiteral(src, name, label = 'the source') {
  const start = _objectStart(src, name, label);
  return src.slice(_tableOpen(src, start),
                   _closingBrace(src, start, name, label) + 1);
}

/** Read `file` and extract from it, using its name in error messages. */
function extractFrom(file, names) {
  const fs = require('fs');
  const path = require('path');
  return extractFunctions(fs.readFileSync(file, 'utf8'), names, path.basename(file));
}

/**
 * Build a callable from extracted source.
 *
 * `preamble` supplies whatever the functions close over in the real app
 * -- constants, stubs for collaborators. `returns` is the expression
 * whose value comes back, normally the entry-point function's name.
 */
function buildCallable(file, names, { preamble = '', returns } = {}) {
  return new Function(`
${preamble}
${extractFrom(file, names)}
return ${returns || names[names.length - 1]};
`)();
}

module.exports = { extractFunction, extractFunctions, extractObject,
                   extractObjectLiteral, extractFrom, buildCallable };
