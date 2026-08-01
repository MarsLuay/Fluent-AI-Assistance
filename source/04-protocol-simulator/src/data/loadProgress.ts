export type ZeiaLoadPhase =
  | "reading"
  | "opening"
  | "primary"
  | "connectors"
  | "meshes"
  | "images"
  | "finalizing";

export type ZeiaLoadProgress = {
  phase: ZeiaLoadPhase;
  message: string;
  percent: number;
  current: number;
  total: number;
  fileName?: string;
  entryName?: string;
  etaSeconds?: number | null;
};

export type LoadProgressReporter = (progress: ZeiaLoadProgress) => void;

const PHASE_WEIGHTS: Record<ZeiaLoadPhase, number> = {
  reading: 0.03,
  opening: 0.05,
  primary: 0.22,
  connectors: 0.18,
  meshes: 0.42,
  images: 0.07,
  finalizing: 0.03
};

const PHASE_ORDER: ZeiaLoadPhase[] = ["reading", "opening", "primary", "connectors", "meshes", "images", "finalizing"];

export function createLoadProgressReporter(onProgress?: LoadProgressReporter, fileName = ""): {
  reportPhase: (phase: ZeiaLoadPhase, message: string, phaseProgress: number, current: number, total: number, entryName?: string) => void;
  complete: (message: string) => void;
} {
  const startMs = Date.now();

  const reportPhase = (
    phase: ZeiaLoadPhase,
    message: string,
    phaseProgress: number,
    current: number,
    total: number,
    entryName?: string
  ) => {
    if (!onProgress) return;
    const clampedPhaseProgress = Math.min(1, Math.max(0, phaseProgress));
    const percent = overallPercent(phase, clampedPhaseProgress);
    onProgress({
      phase,
      message,
      percent,
      current,
      total,
      fileName: fileName || undefined,
      entryName,
      etaSeconds: estimateRemainingSeconds(startMs, percent)
    });
  };

  const complete = (message: string) => {
    if (!onProgress) return;
    onProgress({
      phase: "finalizing",
      message,
      percent: 100,
      current: 1,
      total: 1,
      fileName: fileName || undefined,
      etaSeconds: 0
    });
  };

  return { reportPhase, complete };
}

export function composeMultiFileProgressReporter(
  reporter: LoadProgressReporter | undefined,
  fileIndex: number,
  fileCount: number,
  fileName: string
): LoadProgressReporter | undefined {
  if (!reporter) return undefined;
  return (update) => {
    const sliceStart = (fileIndex / fileCount) * 100;
    const sliceWidth = 100 / fileCount;
    const scaledPercent = Math.round(sliceStart + (update.percent / 100) * sliceWidth);
    reporter({
      ...update,
      percent: fileCount > 1 ? Math.min(100, scaledPercent) : update.percent,
      fileName,
      message: fileCount > 1 ? `${update.message} (${fileIndex + 1} of ${fileCount})` : update.message
    });
  };
}

function overallPercent(phase: ZeiaLoadPhase, phaseProgress: number): number {
  let base = 0;
  for (const step of PHASE_ORDER) {
    if (step === phase) {
      return Math.min(99, Math.round((base + PHASE_WEIGHTS[step] * phaseProgress) * 100));
    }
    base += PHASE_WEIGHTS[step];
  }
  return 99;
}

function estimateRemainingSeconds(startMs: number, percent: number): number | null {
  if (percent <= 2 || percent >= 100) return null;
  const elapsedSeconds = (Date.now() - startMs) / 1000;
  const estimatedTotalSeconds = elapsedSeconds / (percent / 100);
  const remaining = estimatedTotalSeconds - elapsedSeconds;
  if (!Number.isFinite(remaining) || remaining <= 0) return null;
  return Math.max(1, Math.ceil(remaining));
}

export function formatEtaSeconds(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds < 60) return `~${seconds}s remaining`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (remainder === 0) return `~${minutes}m remaining`;
  return `~${minutes}m ${remainder}s remaining`;
}

/** Share of total in-app startup attributed to the server sample-registry build. */
export const STARTUP_REGISTRY_WEIGHT = 18;

/** Share of total in-app startup attributed to loading the active dataset/ZEIA. */
export const STARTUP_DATASET_WEIGHT = 100 - STARTUP_REGISTRY_WEIGHT;

export type AppStartupPhase = "registry" | "dataset";

export type RegistryStartupProgress = {
  status: "idle" | "building" | "ready";
  percent: number;
  message: string;
};

export type AppStartupProgress = {
  phase: AppStartupPhase;
  message: string;
  /** Overall startup completion from 0-100. */
  percent: number;
  /** Raw phase percent before weighting (registry 0-100 or ZEIA import 0-100). */
  phasePercent: number;
  zeia?: ZeiaLoadProgress | null;
};

export function composeAppStartupProgress(
  phase: AppStartupPhase,
  phasePercent: number,
  message: string,
  zeia?: ZeiaLoadProgress | null
): AppStartupProgress {
  const clampedPhasePercent = Math.min(100, Math.max(0, Math.round(phasePercent)));
  const overall =
    phase === "registry"
      ? Math.round((clampedPhasePercent / 100) * STARTUP_REGISTRY_WEIGHT)
      : Math.round(STARTUP_REGISTRY_WEIGHT + (clampedPhasePercent / 100) * STARTUP_DATASET_WEIGHT);

  return {
    phase,
    message,
    percent: Math.min(100, overall),
    phasePercent: clampedPhasePercent,
    zeia: phase === "dataset" ? zeia ?? null : null
  };
}
