export interface TrackSummaryCandidate {
  videoId: string;
  trackId: string;
  status: string;
  similarity: number | null;
  margin: number | null;
  userEmail: string | null;
  threshold: number | null;
}
