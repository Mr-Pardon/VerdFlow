export interface PackageImportFailure {
  ordinal: number;
  sourceName: string;
  message: string;
}

export interface PackageImportOutcome<Value> {
  imported: Value[];
  failures: PackageImportFailure[];
}

export const importPackagesSequentially = async <Value>(
  sourcePaths: readonly string[],
  importOne: (sourcePath: string) => Promise<Value>,
  readableError: (error: unknown) => string,
): Promise<PackageImportOutcome<Value>> => {
  const imported: Value[] = [];
  const failures: PackageImportFailure[] = [];
  for (const [index, sourcePath] of sourcePaths.entries()) {
    try {
      imported.push(await importOne(sourcePath));
    } catch (error) {
      failures.push({
        ordinal: index + 1,
        sourceName: packageFileName(sourcePath),
        message: readableError(error),
      });
    }
  }
  return { imported, failures };
};

export const packageImportFailureMessage = (
  packageLabel: string,
  failures: readonly PackageImportFailure[],
  importedCount: number,
) => {
  const detail = failures
    .slice(0, 3)
    .map((failure) => `${failure.sourceName || `第 ${failure.ordinal} 个包`}：${failure.message}`)
    .join("；");
  const omitted = failures.length > 3 ? `；另有 ${failures.length - 3} 个失败` : "";
  const success = importedCount ? `已成功处理 ${importedCount} 个；` : "";
  return `${success}${failures.length} 个${packageLabel}导入失败：${detail}${omitted}`;
};

export const unsupportedPackageFailures = (
  sourceNames: readonly string[],
  extension: string,
): PackageImportFailure[] => sourceNames.map((sourceName, index) => ({
  ordinal: index + 1,
  sourceName,
  message: `文件类型不受支持，仅接受 .${extension}`,
}));

const packageFileName = (sourcePath: string) =>
  sourcePath.split(/[\\/]/).filter(Boolean).at(-1) ?? sourcePath;
