import { callWith } from "./fleet.ts";

// Bytes of the archive in each piece sent: a multiple of three, so that the
// pieces of base64 join into one valid text.
const PIECE = 3 * 64 * 1024;

export interface Uploaded {
    run_id: string;
    filename: string;
    size: number;
    sha256: string;
}

export function base64Pieces(bytes: Uint8Array, size: number = PIECE): string[] {
    const pieces: string[] = [];
    for (let start = 0; start < bytes.length; start += size) {
        let binary = "";
        // In small steps: a call takes only so many arguments.
        for (let at = start; at < Math.min(start + size, bytes.length); at += 8192) {
            binary += String.fromCharCode(...bytes.subarray(at, Math.min(at + 8192, start + size)));
        }
        pieces.push(btoa(binary));
    }
    return pieces;
}

// The parameters of project.upload, as the pieces of one JSON object.
export function uploadPieces(id: string, filename: string, bytes: Uint8Array, size: number = PIECE): string[] {
    return [`{"id":${JSON.stringify(id)},"filename":${JSON.stringify(filename)},"data":"`, ...base64Pieces(bytes, size), '"}'];
}

// Sends an archive as the new files of a project; a job unpacks it.
export async function uploadArchive(id: string, file: File): Promise<Uploaded> {
    const bytes = new Uint8Array(await file.arrayBuffer());
    return callWith<Uploaded>("project.upload", uploadPieces(id, file.name, bytes));
}
