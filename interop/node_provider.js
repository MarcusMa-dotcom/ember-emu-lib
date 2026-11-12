// The provider of emberplus-connection, for the consumer of emberemu to read.
const { EmberServer, Model } = require('emberplus-connection')
const {
  NumberedTreeNodeImpl, EmberNodeImpl, ParameterImpl, ParameterType, EmberFunctionImpl,
  ParameterAccess, MatrixImpl, MatrixType, MatrixAddressingMode,
} = Model
const port = Number(process.argv[2]); const seconds = Number(process.argv[3])
const log = (...parts) => console.log('JS', ...parts)
const s = new EmberServer(port, '127.0.0.1')
s.on('error', (e) => log('server error', e.message || e))
s.onInvocation = async (emberFunction, invocation) => {
  const args = invocation.contents.invocation.args.map((a) => a.value)
  log('invoke', emberFunction.contents.identifier, JSON.stringify(args))
  return { id: invocation.contents.invocation.id, success: true, result: [{ type: ParameterType.Integer, value: args.reduce((a, b) => a + b, 0) }] }
}
s.onSetValue = async (node, value) => {
  log('set', node.contents.identifier, JSON.stringify(Buffer.isBuffer(value) ? value.toString('hex') : value))
  if (node.contents.identifier === 'Locked') return false
  s.update(node, { value })
  return true
}
s.onMatrixOperation = async (matrix, connections) => {
  for (const connection of Object.values(connections)) {
    log('matrix', matrix.contents.identifier, JSON.stringify(connection))
    s.updateMatrixConnection(matrix, connection)
  }
}
const P = (n, type, id, value, access, extra = {}) => new NumberedTreeNodeImpl(n, new ParameterImpl(
  type, id, extra.description, value, extra.maximum, extra.minimum, access, extra.format, extra.enumeration,
  extra.factor, undefined, undefined, extra.step, undefined, extra.streamIdentifier))
const tree = {
  1: new NumberedTreeNodeImpl(1, new EmberNodeImpl('Root', 'The root node', undefined, true), {
    1: new NumberedTreeNodeImpl(1, new EmberNodeImpl('Values', undefined, undefined, true), {
      1: P(1, ParameterType.Integer, 'Count', 2, ParameterAccess.ReadWrite, { minimum: 0, maximum: 10, step: 1 }),
      2: P(2, ParameterType.Real, 'Gain', -12.5, ParameterAccess.ReadWrite, { minimum: -60, maximum: 12, format: '%.1f dB', description: 'Input gain' }),
      3: P(3, ParameterType.String, 'Label', 'Grüße', ParameterAccess.ReadWrite),
      4: P(4, ParameterType.Boolean, 'Mute', true, ParameterAccess.ReadWrite),
      5: P(5, ParameterType.Enum, 'Mode', 1, ParameterAccess.ReadWrite, { enumeration: 'Off\nMono\nStereo' }),
      6: P(6, ParameterType.String, 'Name', 'Sofie', ParameterAccess.Read),
      7: P(7, ParameterType.Integer, 'Ticks', 0, ParameterAccess.Read),
      8: P(8, ParameterType.Integer, 'Locked', 5, ParameterAccess.ReadWrite),
      9: P(9, ParameterType.Real, 'Whole', 3, ParameterAccess.ReadWrite),
    }),
    2: new NumberedTreeNodeImpl(2, new EmberNodeImpl('Functions', undefined, undefined, true), {
      1: new NumberedTreeNodeImpl(1, new EmberFunctionImpl('Add', 'Adds two numbers',
        [{ type: ParameterType.Integer, name: 'a' }, { type: ParameterType.Integer, name: 'b' }],
        [{ type: ParameterType.Integer, name: 'sum' }])),
    }),
    3: new NumberedTreeNodeImpl(3, new EmberNodeImpl('Matrices', undefined, undefined, true), {
      1: new NumberedTreeNodeImpl(1, new MatrixImpl('Test Matrix', [1, 2, 3, 4, 5], [1, 2, 3, 4, 5], {}, undefined,
        MatrixType.NToN, MatrixAddressingMode.NonLinear, 5, 5)),
    }),
    4: new NumberedTreeNodeImpl(4, new EmberNodeImpl('Empty', undefined, undefined, true), {}),
    5: new NumberedTreeNodeImpl(5, new EmberNodeImpl('Offline', undefined, undefined, false), {}),
  }),
}
s.init(tree).then(() => {
  log('serving on', port)
  let n = 0
  const timer = setInterval(() => {
    n += 1
    s.update(s.getElementByPath('1.1.7'), { value: n })      // a value that changes by itself
  }, 300)
  setTimeout(() => { clearInterval(timer); s.discard(); log('done'); process.exit(0) }, seconds * 1000)
}).catch((e) => { log('init failed', e); process.exit(2) })
