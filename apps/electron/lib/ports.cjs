"use strict";

const net = require("net");

function getFreePort(host = "127.0.0.1") {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.on("error", reject);
    s.listen(0, host, () => {
      const addr = s.address();
      const port = typeof addr === "object" && addr ? addr.port : null;
      s.close((err) => {
        if (err) reject(err);
        else if (port) resolve(port);
        else reject(new Error("Could not allocate port"));
      });
    });
  });
}

module.exports = { getFreePort };
