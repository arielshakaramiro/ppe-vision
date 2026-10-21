import { api } from "../../../api";

// Upload bertahap: potong file jadi beberapa bagian, bisa dijeda & dilanjutkan dari offset terakhir.
// `uploadId` dipakai ulang supaya jeda/lanjut tidak memulai sesi baru. Offset benar diambil dari
// server (GET /api/uploads/{id}) sebelum mengirim lagi, jadi tahan terhadap koneksi putus.
export async function uploadVideo(
  file: File,
  idRef: { current: string | null },
  onProgress: (pct: number) => void,
  shouldStop: () => boolean
): Promise<string> {
  let chunkMb = 10;
  if (!idRef.current) {
    const start = await api.startUpload(file.name, file.size);
    idRef.current = start.id;
    chunkMb = start.chunk_mb || 10;
  }
  const id = idRef.current;
  const chunk = chunkMb * 1024 * 1024;
  let offset = (await api.uploadStatus(id)).offset;
  while (offset < file.size) {
    if (shouldStop()) throw new Error("dijeda");
    const slice = file.slice(offset, offset + chunk);
    const buf = await slice.arrayBuffer();
    const r = await api.uploadChunk(id, offset, buf);
    offset = r.offset;
    onProgress(Math.round((offset / file.size) * 100));
  }
  const done = await api.completeUpload(id);
  idRef.current = null;
  return done.id;
}
