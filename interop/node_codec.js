// Decode what emberemu encoded, and encode the same values with emberplus-connection.
//
//   node node_codec.js mine.json theirs.json
//
// mine.json holds values with the bytes emberemu makes of them. theirs.json gets the
// same values with the bytes emberplus-connection makes of them.
const fs = require('fs')
const { berEncode, berDecode, Model } = require('emberplus-connection')
const { ParameterImpl, ParameterType, QualifiedElementImpl } = Model

const items = JSON.parse(fs.readFileSync(process.argv[2]))
const types = {
  real: ParameterType.Real,
  integer: ParameterType.Integer,
  string: ParameterType.String,
  boolean: ParameterType.Boolean,
}
// emberplus-connection keeps integers in 32 bits. Ember+ allows 64.
const within32 = (item) => item.kind !== 'integer' || (item.value >= -(2 ** 31) && item.value < 2 ** 31)

let failed = 0
let beyond = 0
const out = []
for (const item of items) {
  const decoded = berDecode(Buffer.from(item.hex, 'hex'))
  const element = decoded.value && Object.values(decoded.value)[0]
  const got = element && element.contents.value
  if (!within32(item)) {
    beyond++
  } else if (got !== item.value || (decoded.errors && decoded.errors.length)) {
    failed++
    console.log('JS FAIL decodes differently:', item.kind, item.value, '->', got)
  }
  const theirs = berEncode(
    [new QualifiedElementImpl('1.2.3', new ParameterImpl(types[item.kind], undefined, undefined, item.value))], 0)
  out.push({ kind: item.kind, value: item.value, hex: theirs.toString('hex'), mine: item.hex })
}
fs.writeFileSync(process.argv[3], JSON.stringify(out))
console.log(`JS decoded ${items.length - beyond} encodings of emberemu, ${failed} differently`
  + ` (${beyond} integers beyond 32 bits left out, this library cannot hold them)`)
process.exit(failed ? 1 : 0)
