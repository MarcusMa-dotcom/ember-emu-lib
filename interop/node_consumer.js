// The consumer of emberplus-connection against a provider of emberemu.
const { EmberClient } = require('emberplus-connection')
const port = Number(process.argv[2])
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
const check = (name, ok, detail) => { results.push(ok); console.log(`JS ${ok ? 'ok  ' : 'FAIL'} ${name}${detail !== undefined ? ' -> ' + JSON.stringify(detail) : ''}`) }

async function main() {
  const client = new EmberClient('127.0.0.1', port)
  client.on('error', (e) => console.log('JS client error:', e.message || e))
  const streams = []
  client.on('streamUpdate', (path, value) => streams.push([path, value]))
  const err = await client.connect()
  check('connect', !(err instanceof Error), err && err.message)
  await (await client.getDirectory(client.tree)).response
  check('root listing', Object.keys(client.tree).length === 1, Object.values(client.tree).map((n) => n.contents.identifier))
  // Bytes first: this client keeps a view into its receive buffer, which later messages overwrite.
  const early = await client.getElementByPath('Device.Audio.Blob')
  check('octets', Buffer.isBuffer(early.contents.value) && early.contents.value.toString('hex') === '01feff', early.contents.value && early.contents.value.toString('hex'))
  await client.expand(client.tree)
  let count = 0
  const walk = (nodes, depth) => { for (const n of Object.values(nodes || {})) { count++; walk(n.children, depth + 1) } }
  walk(client.tree, 0)
  check('expand whole tree', count === 146, count)

  const by = async (p) => client.getElementByPath(p)
  const gain = await by('Device.Audio.Gain')
  check('gain contents', gain && gain.contents.value === -12 && gain.contents.minimum === -60 && gain.contents.maximum === 12
    && gain.contents.format === '%.1f dB' && gain.contents.description === 'Input gain' && gain.contents.access === 'READ_WRITE',
    gain && { value: gain.contents.value, min: gain.contents.minimum, max: gain.contents.maximum, format: gain.contents.format, access: gain.contents.access, type: gain.contents.parameterType })
  const name = await by('Device.Identity.Name')
  check('string read only', name.contents.value === 'Studio A' && name.contents.access === 'READ', { value: name.contents.value, access: name.contents.access })
  const mode = await by('Device.Audio.Mode')
  check('enum', mode.contents.value === 1 && mode.contents.enumeration === 'Off\nMono\nStereo', { value: mode.contents.value, enumeration: mode.contents.enumeration, type: mode.contents.parameterType })
  const standard = await by('Device.Audio.Standard')
  const entries = standard.contents.enumMap instanceof Map ? [...standard.contents.enumMap.entries()] : standard.contents.enumMap
  check('enum map', standard.contents.value === 50 && JSON.stringify(entries) === '[["1080i50",25],["1080p50",50]]', { value: standard.contents.value, entries })
  const fader = await by('Device.Audio.Fader')
  check('factor', fader.contents.factor === 10 && fader.contents.value === -125, { factor: fader.contents.factor, value: fader.contents.value })
  const count5 = await by('Device.Audio.Count')
  check('integer step', count5.contents.value === 5 && count5.contents.step === 1, { value: count5.contents.value, step: count5.contents.step })
  const blob = await by('Device.Audio.Blob')
  const fire = await by('Device.Audio.Fire')
  check('trigger type', fire.contents.parameterType === 'TRIGGER', fire.contents.parameterType)
  const offline = await by('Device.Offline')
  check('offline node', offline.contents.isOnline === false, offline.contents.isOnline)
  const big = await by('Device.Big')
  check('large listing over several frames', Object.keys(big.children).length === 120, Object.keys(big.children).length)
  const level = await by('Device.Audio.Level')
  check('stream identifier', level.contents.streamIdentifier === 7, level.contents.streamIdentifier)

  // writing
  // The client answers a request with the first report about the element, which may be the answer to an
  // earlier question. So wait a moment and read the value from the tree the client keeps.
  const set = async (node, value) => { await (await client.setValue(node, value)).response; await sleep(150); return node.contents.value }
  check('set real', (await set(gain, -6.5)) === -6.5)
  check('set boolean', (await set(await by('Device.Audio.Mute'), true)) === true)
  check('set enum', (await set(mode, 2)) === 2)
  check('set string', (await set(await by('Device.Audio.Label'), 'Gäste 1')) === 'Gäste 1')
  check('set integer', (await set(count5, 7)) === 7)
  const newBlob = await set(blob, Buffer.from([0xfd, 0xfe, 0xff, 0xf8, 0x00]))
  check('set octets', Buffer.isBuffer(newBlob) && newBlob.toString('hex') === 'fdfefff800', newBlob && newBlob.toString('hex'))
  check('set read only is refused', (await set(name, 'Hacked')) === 'Studio A')
  check('set out of range is refused', (await set(gain, 99.5)) === -6.5)
  check('set integer out of range is refused', (await set(count5, 99)) === 7)
  await (await client.setValue(fire, true, false)).response
  check('fire trigger', true)

  // function
  const add = await by('Device.Add')
  const sum = await (await client.invoke(add, { type: 'INTEGER', value: 2 }, { type: 'INTEGER', value: 40 })).response
  check('invoke', sum && sum.success === true && sum.result && sum.result[0].value === 42, sum)
  const boom = await (await client.invoke(await by('Device.Boom'))).response
  check('invoke failing function', boom && boom.success === false, boom)

  // matrix
  const router = await by('Device.Router')
  await (await client.getDirectory(router)).response
  check('matrix contents', router.contents.targetCount === 4 && router.contents.sourceCount === 4, { type: router.contents.matrixType, mode: router.contents.addressingMode, connections: router.contents.connections })
  const connected = await (await client.matrixConnect(router, 2, [3])).response
  check('matrix connect', connected && connected.contents.connections[2] && connected.contents.connections[2].sources[0] === 3, connected && connected.contents.connections)
  const mix = await by('Device.Mix')
  await (await client.getDirectory(mix)).response
  await (await client.matrixSetConnection(mix, 0, [0, 1])).response
  await (await client.matrixConnect(mix, 0, [2])).response
  const mixed = await (await client.matrixDisconnect(mix, 0, [0])).response
  check('n-to-n matrix', mixed && JSON.stringify(mixed.contents.connections[0].sources) === '[1,2]', mixed && mixed.contents.connections)

  // changes made by the provider itself arrive without asking
  const serial = await by('Device.Identity.Serial')
  const levelInt = await by('Device.Meters.LevelInteger')
  const left = await by('Device.Meters.Left')
  const right = await by('Device.Meters.Right')
  check('stream descriptor', left.contents.streamDescriptor && left.contents.streamDescriptor.offset === 0
    && right.contents.streamDescriptor.offset === 4, [left.contents.streamDescriptor, right.contents.streamDescriptor])
  for (const node of [level, levelInt, left, right]) await (await client.subscribe(node)).response
  await sleep(2600)
  check('provider change arrives (integer)', serial.contents.value === 777, serial.contents.value)
  check('provider change arrives (real)', gain.contents.value === 2.5, gain.contents.value)
  const of = (path) => streams.filter(([p]) => p === path).map(([, v]) => v)
  const ints = of(client.getInternalNodePath(levelInt)), lefts = of(client.getInternalNodePath(left)), rights = of(client.getInternalNodePath(right))
  check('stream of whole numbers arrives', ints.length >= 5 && ints[ints.length - 1] > -400 && Number.isInteger(ints[0]), { updates: ints.length, last: ints[ints.length - 1] })
  check('stream block: value at byte 0', lefts.length >= 5 && lefts[lefts.length - 1] > -40 && lefts[lefts.length - 1] % 0.5 === 0, { updates: lefts.length, last: lefts[lefts.length - 1] })
  check('stream block: value at byte 4', rights.length >= 5 && rights[rights.length - 1] < -20 && rights[rights.length - 1] % 0.25 === 0, { updates: rights.length, last: rights[rights.length - 1] })
  console.log('JS note stream of reals: ' + of(client.getInternalNodePath(level)).length + ' updates (this client reads only whole numbers and byte blocks)')
  await client.disconnect()
  console.log(`JS done: ${results.filter(Boolean).length} of ${results.length} checks passed`)
  process.exit(results.every(Boolean) ? 0 : 1)
}
main().catch((e) => { console.log('JS crashed:', e); process.exit(2) })
