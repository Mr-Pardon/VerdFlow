import { createWriteStream } from "node:fs";
import http from "node:http";
import { stat } from "node:fs/promises";
import { pipeline } from "node:stream/promises";


export const downloadLocalApiFile = async ({ target, headers, destination }) => {
  const expectedBytes = await new Promise((resolve, reject) => {
    const request = http.request(
      target,
      { method: "GET", headers },
      (response) => {
        const status = response.statusCode ?? 0;
        if (status < 200 || status >= 300) {
          response.resume();
          reject(new Error(`导出文件读取失败，本地服务返回 ${status}`));
          return;
        }
        const contentLength = response.headers["content-length"];
        const parsedLength =
          typeof contentLength === "string" ? Number(contentLength) : null;
        pipeline(
          response,
          createWriteStream(destination, { flags: "wx" }),
        )
          .then(() => resolve(Number.isSafeInteger(parsedLength) ? parsedLength : null))
          .catch(reject);
      },
    );
    request.once("error", reject);
    request.end();
  });

  if (expectedBytes !== null) {
    const written = await stat(destination);
    if (written.size !== expectedBytes) {
      throw new Error(
        `导出文件不完整：预期 ${expectedBytes} 字节，实际 ${written.size} 字节`,
      );
    }
  }
};
