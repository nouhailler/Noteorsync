import { deriveArgon2idKek, ensureCryptoReady } from './crypto';

export interface Argon2WorkerRequest {
  id: number;
  passwordUtf8: string;
  salt16: ArrayBuffer;
  memoryKib: number;
  iterations: number;
  parallelism: number;
}

export interface Argon2WorkerResponse {
  id: number;
  kek?: ArrayBuffer;
  error?: string;
}

self.onmessage = async (event: MessageEvent<Argon2WorkerRequest>) => {
  const { id, passwordUtf8, salt16, memoryKib, iterations, parallelism } = event.data;
  try {
    await ensureCryptoReady();
    const kek = await deriveArgon2idKek(
      passwordUtf8,
      new Uint8Array(salt16),
      memoryKib,
      iterations,
      parallelism,
    );
    const out = kek.buffer.slice(kek.byteOffset, kek.byteOffset + kek.byteLength) as ArrayBuffer;
    kek.fill(0);
    (self as unknown as Worker).postMessage({ id, kek: out } satisfies Argon2WorkerResponse, [out]);
  } catch (err) {
    (self as unknown as Worker).postMessage({
      id,
      error: err instanceof Error ? err.message : 'Argon2id worker failure',
    } satisfies Argon2WorkerResponse);
  }
};
