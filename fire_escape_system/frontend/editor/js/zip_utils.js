const encoder = new TextEncoder();

const crcTable = (() => {
    const table = new Uint32Array(256);
    for (let index = 0; index < 256; index += 1) {
        let value = index;
        for (let bit = 0; bit < 8; bit += 1) {
            value = (value & 1) ? (0xEDB88320 ^ (value >>> 1)) : (value >>> 1);
        }
        table[index] = value >>> 0;
    }
    return table;
})();

function crc32(bytes) {
    let crc = 0xFFFFFFFF;
    for (const byte of bytes) crc = crcTable[(crc ^ byte) & 0xFF] ^ (crc >>> 8);
    return (crc ^ 0xFFFFFFFF) >>> 0;
}

function asBytes(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    return encoder.encode(String(value));
}

function concat(parts) {
    const size = parts.reduce((sum, part) => sum + part.length, 0);
    const output = new Uint8Array(size);
    let offset = 0;
    parts.forEach((part) => {
        output.set(part, offset);
        offset += part.length;
    });
    return output;
}

function header(size) {
    const bytes = new Uint8Array(size);
    return { bytes, view: new DataView(bytes.buffer) };
}

function dosDateTime(date = new Date()) {
    const year = Math.max(1980, date.getFullYear());
    const time = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
    const day = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
    return { time, day };
}

export function createStoredZip(files) {
    const localParts = [];
    const centralParts = [];
    let localOffset = 0;
    const now = dosDateTime();

    Object.entries(files).forEach(([name, value]) => {
        const nameBytes = encoder.encode(name.replaceAll('\\', '/'));
        const data = asBytes(value);
        const crc = crc32(data);

        const local = header(30);
        local.view.setUint32(0, 0x04034B50, true);
        local.view.setUint16(4, 20, true);
        local.view.setUint16(6, 0x0800, true);
        local.view.setUint16(8, 0, true);
        local.view.setUint16(10, now.time, true);
        local.view.setUint16(12, now.day, true);
        local.view.setUint32(14, crc, true);
        local.view.setUint32(18, data.length, true);
        local.view.setUint32(22, data.length, true);
        local.view.setUint16(26, nameBytes.length, true);
        local.view.setUint16(28, 0, true);
        localParts.push(local.bytes, nameBytes, data);

        const central = header(46);
        central.view.setUint32(0, 0x02014B50, true);
        central.view.setUint16(4, 20, true);
        central.view.setUint16(6, 20, true);
        central.view.setUint16(8, 0x0800, true);
        central.view.setUint16(10, 0, true);
        central.view.setUint16(12, now.time, true);
        central.view.setUint16(14, now.day, true);
        central.view.setUint32(16, crc, true);
        central.view.setUint32(20, data.length, true);
        central.view.setUint32(24, data.length, true);
        central.view.setUint16(28, nameBytes.length, true);
        central.view.setUint16(30, 0, true);
        central.view.setUint16(32, 0, true);
        central.view.setUint16(34, 0, true);
        central.view.setUint16(36, 0, true);
        central.view.setUint32(38, 0, true);
        central.view.setUint32(42, localOffset, true);
        centralParts.push(central.bytes, nameBytes);

        localOffset += local.bytes.length + nameBytes.length + data.length;
    });

    const centralBytes = concat(centralParts);
    const end = header(22);
    const count = Object.keys(files).length;
    end.view.setUint32(0, 0x06054B50, true);
    end.view.setUint16(4, 0, true);
    end.view.setUint16(6, 0, true);
    end.view.setUint16(8, count, true);
    end.view.setUint16(10, count, true);
    end.view.setUint32(12, centralBytes.length, true);
    end.view.setUint32(16, localOffset, true);
    end.view.setUint16(20, 0, true);

    return new Blob([...localParts, centralBytes, end.bytes], { type: 'application/zip' });
}
