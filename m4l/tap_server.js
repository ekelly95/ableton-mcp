// AbletonMCP Tap — Node for Max script.
// Receives level messages from the Max patch and serves snapshots over TCP
// (127.0.0.1:9878, length-prefixed JSON — same wire protocol as the bridge).
// Stdlib only: never needs `script npm install`. Conservative JS on purpose —
// the bundled Node version varies with the Max version.

var net = require("net");

// Outside Max (tests, manual runs) max-api doesn't exist — stub it so the
// framing/protocol layer stays testable with plain Node.
var maxApi;
try {
  maxApi = require("max-api");
} catch (e) {
  maxApi = {
    addHandler: function () {},
    post: function (msg) {
      console.error("[tap]", msg);
    },
    outlet: function () {}
  };
}

var TAP_PROTOCOL_VERSION = 1;
var HOST = "127.0.0.1"; // loopback only: no firewall prompt, local trust model
// Env override exists for tests only (inside Max there is no env to set).
var PORT = parseInt(process.env.ABLETON_TAP_PORT || "9878", 10);
var MAX_MESSAGE_SIZE = 1024 * 1024;
var WINDOW_MS = 5000; // rolling stats window
var SILENCE_MS = 3000; // all-zero for this long => receiving_audio false
var DB_FLOOR = -70.0;

var BAND_LABELS = ["60", "120", "240", "480", "960", "1.9k", "3.8k", "7.7k"];

var startedAt = Date.now();
var state = {
  bands: [0, 0, 0, 0, 0, 0, 0, 0], // linear RMS per band (post mono-sum)
  rms: 0, // linear RMS of the mono sum
  peak: [0, 0], // linear peak L, R
  lastNonZeroAt: 0,
  history: [], // {t, rms, peak0, peak1}
  msgsOk: 0,
  msgsBad: 0,
  lastBadSample: null
};

// Max message args normally arrive as numbers, but a patch defect can send
// strings/garbage — coerce hard and count, so ping can diagnose remotely.
function num(value) {
  var f = typeof value === "number" ? value : parseFloat(value);
  if (typeof f !== "number" || isNaN(f) || !isFinite(f)) {
    state.msgsBad += 1;
    if (state.lastBadSample === null) state.lastBadSample = String(value);
    return null;
  }
  state.msgsOk += 1;
  return f;
}

function toDb(linear) {
  if (typeof linear !== "number" || isNaN(linear) || linear <= 0) return DB_FLOOR;
  var db = 20 * Math.log10(linear);
  if (isNaN(db) || db < DB_FLOOR) return DB_FLOOR;
  return Math.round(db * 10) / 10;
}

function noteActivity(value) {
  if (value > 0.00001) state.lastNonZeroAt = Date.now();
}

function pushHistory() {
  var now = Date.now();
  state.history.push({ t: now, rms: state.rms, peak0: state.peak[0], peak1: state.peak[1] });
  while (state.history.length > 0 && now - state.history[0].t > WINDOW_MS) {
    state.history.shift();
  }
}

maxApi.addHandler("band", function (index, value) {
  var i = Math.floor(num(index) || 0);
  var v = num(value);
  if (v !== null && i >= 0 && i < state.bands.length) {
    state.bands[i] = v;
    noteActivity(v);
  }
});

maxApi.addHandler("rms", function (value) {
  var v = num(value);
  if (v === null) return;
  state.rms = v;
  noteActivity(v);
  pushHistory(); // rms arrives at a steady ~10 Hz; use it as the history clock
});

maxApi.addHandler("peak", function (channel, value) {
  var ch = Math.floor(num(channel) || 0);
  var v = num(value);
  if (v !== null && (ch === 0 || ch === 1)) {
    state.peak[ch] = v;
    noteActivity(v);
  }
});

function windowStats() {
  var n = state.history.length;
  if (n === 0) return { rms_mean_db: DB_FLOOR, rms_max_db: DB_FLOOR, peak_max_db: DB_FLOOR };
  var sum = 0;
  var rmsMax = 0;
  var peakMax = 0;
  for (var i = 0; i < n; i++) {
    var h = state.history[i];
    sum += h.rms;
    if (h.rms > rmsMax) rmsMax = h.rms;
    if (h.peak0 > peakMax) peakMax = h.peak0;
    if (h.peak1 > peakMax) peakMax = h.peak1;
  }
  return {
    rms_mean_db: toDb(sum / n),
    rms_max_db: toDb(rmsMax),
    peak_max_db: toDb(peakMax)
  };
}

function snapshot() {
  var bandsDb = [];
  for (var i = 0; i < state.bands.length; i++) {
    bandsDb.push({ hz: BAND_LABELS[i], level_db: toDb(state.bands[i]) });
  }
  var stats = windowStats();
  return {
    receiving_audio: Date.now() - state.lastNonZeroAt < SILENCE_MS,
    rms_db: toDb(state.rms),
    peak_db: [toDb(state.peak[0]), toDb(state.peak[1])],
    clipping: state.peak[0] >= 0.999 || state.peak[1] >= 0.999,
    bands: bandsDb,
    window_seconds: WINDOW_MS / 1000,
    window: stats,
    note: "Tap sits pre-master-fader: readings ignore the master fader position."
  };
}

function handleRequest(request) {
  var id = request && request.id ? request.id : "";
  var type = request && request.type ? request.type : "";
  if (type === "ping") {
    return {
      status: "success",
      id: id,
      result: {
        pong: true,
        name: "AbletonMCP Tap",
        tap_protocol_version: TAP_PROTOCOL_VERSION,
        uptime_seconds: Math.round((Date.now() - startedAt) / 1000),
        msgs_ok: state.msgsOk,
        msgs_bad: state.msgsBad,
        last_bad_sample: state.lastBadSample
      }
    };
  }
  if (type === "get_levels") {
    return { status: "success", id: id, result: snapshot() };
  }
  return {
    status: "error",
    id: id,
    error: "Unknown tap command: " + type,
    error_type: "ValidationError"
  };
}

// --- length-prefixed JSON framing over TCP (multiple concurrent clients) ---

function serveConnection(socket) {
  var buffer = Buffer.alloc(0);
  socket.on("data", function (chunk) {
    buffer = Buffer.concat([buffer, chunk]);
    while (buffer.length >= 4) {
      var length = buffer.readUInt32BE(0);
      if (length > MAX_MESSAGE_SIZE) {
        socket.destroy();
        return;
      }
      if (buffer.length < 4 + length) return; // wait for the rest
      var payload = buffer.slice(4, 4 + length);
      buffer = buffer.slice(4 + length);
      var response;
      try {
        response = handleRequest(JSON.parse(payload.toString("utf8")));
      } catch (e) {
        response = { status: "error", error: String(e), error_type: "InternalError" };
      }
      var body = Buffer.from(JSON.stringify(response), "utf8");
      var header = Buffer.alloc(4);
      header.writeUInt32BE(body.length, 0);
      socket.write(Buffer.concat([header, body]));
    }
  });
  socket.on("error", function () {
    /* client vanished; nothing to do */
  });
}

// Bind with retry: duplicating the device / undo / set reload can briefly
// leave the old node process holding the port (transient EADDRINUSE).
var bindAttempts = 0;
function startServer() {
  var server = net.createServer(serveConnection);
  server.on("error", function (err) {
    if (err && err.code === "EADDRINUSE" && bindAttempts < 5) {
      bindAttempts += 1;
      maxApi.post("AbletonMCP Tap: port " + PORT + " busy, retry " + bindAttempts + "/5...");
      setTimeout(startServer, 1000);
    } else {
      maxApi.post("AbletonMCP Tap: FAILED to bind " + PORT + " (" + String(err) + "). Is a second Tap device in the set?");
      maxApi.outlet("status", "PORT BUSY");
    }
  });
  server.listen(PORT, HOST, function () {
    maxApi.post("AbletonMCP Tap: serving on " + HOST + ":" + PORT);
    maxApi.outlet("status", "SERVING " + PORT);
  });
}

maxApi.outlet("status", "STARTING");
startServer();
