interface CryptoWithOptionalUuid {
  randomUUID?: () => string;
  getRandomValues?: (array: Uint8Array) => Uint8Array;
}

function availableCrypto(): CryptoWithOptionalUuid | undefined {
  return typeof globalThis.crypto === 'undefined' ? undefined : globalThis.crypto;
}

/** Create an RFC 4122 version 4 UUID, including on non-secure HTTP origins. */
export function createUuid(cryptoApi: CryptoWithOptionalUuid | null | undefined = availableCrypto()): string {
  if (typeof cryptoApi?.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }

  if (typeof cryptoApi?.getRandomValues !== 'function') {
    throw new Error('A secure random-number generator is unavailable.');
  }

  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
}
