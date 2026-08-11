// AbletonMCP Tap — Node for Max script (protocol v2).
// Receives measurement messages from the Max patch and serves snapshots over
// TCP (127.0.0.1:9878, length-prefixed JSON — same wire protocol as the
// bridge). Stdlib only: never needs `script npm install`. Conservative JS on
// purpose — the bundled Node version varies with the Max version.
//
// v2 measurement convention: the patch squares each channel in MSP and sends
// POWER values — `pow v` is mean(L²+R²) broadband, `bpow i v` the same per
// octave band. JS converts with rms = sqrt(v/2), which equals the true stereo
// RMS and cannot cancel on anti-phase content (the v1 mono-sum bug). The
// per-channel `peak ch v` path is unchanged from v1 (it was always correct).

var net = require("net");

// Outside Max (tests, manual runs) max-api doesn't exist — stub it so the
// framing/protocol layer stays testable with plain Node. The stub records
// handlers so the ABLETON_TAP_TEST_FEED harness (below) can drive them.
var maxApi;
var insideMax = true;
var testHandlers = {};
try {
  maxApi = require("max-api");
} catch (e) {
  insideMax = false;
  maxApi = {
    addHandler: function (name, fn) {
      testHandlers[name] = fn;
    },
    post: function (msg) {
      console.error("[tap]", msg);
    },
    outlet: function () {}
  };
}

var TAP_PROTOCOL_VERSION = 2;
var HOST = "127.0.0.1"; // loopback only: no firewall prompt, local trust model
// Env override exists for tests only (inside Max there is no env to set).
var PORT = parseInt(process.env.ABLETON_TAP_PORT || "9878", 10);
var MAX_MESSAGE_SIZE = 1024 * 1024;
var WINDOW_MS = 5000; // rolling stats window (also the clip-latch window)
var SILENCE_MS = 3000; // no signal above the floor for this long => not receiving
var STALE_MS = 2000; // no MESSAGES for this long => DSP stopped; floor everything
var DB_FLOOR = -70.0;
var CLIP_LINEAR = 0.999; // sample-peak clip threshold (~-0.009 dBFS); not true-peak

// 10 octave bands: fffb~ 10 31.25 2. 1.414 (centers 31.25..16k, Q≈1.414 ≈
// one-octave bandwidth, adjacent bands cross near -3 dB).
var BAND_LABELS = ["31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"];

var startedAt = Date.now();
var state = {
  power: 0, // mean(L²+R²) broadband — rms = sqrt(power/2)
  bandPower: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], // mean(Lk²+Rk²) per band
  peak: [0, 0], // linear sample peak L, R (last ~100ms window)
  lastNonZeroAt: 0,
  lastMessageAt: 0, // wall clock of the last pow/bpow/peak — staleness source
  history: [], // {t, rms, peak0, peak1} at ~10 Hz
  msgsOk: 0,
  msgsBad: 0,
  lastBadSample: null,
  legacyMsgs: 0 // v1 'rms'/'band' messages seen => the DEVICE needs a rebuild
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

function powerToRms(power) {
  return Math.sqrt(Math.max(power, 0) / 2);
}

function noteActivity(value) {
  if (value > 0.00001) state.lastNonZeroAt = Date.now();
}

function pruneHistory(now) {
  while (state.history.length > 0 && now - state.history[0].t > WINDOW_MS) {
    state.history.shift();
  }
}

function pushHistory() {
  var now = Date.now();
  state.history.push({
    t: now,
    rms: powerToRms(state.power),
    peak0: state.peak[0],
    peak1: state.peak[1]
  });
  pruneHistory(now);
}

maxApi.addHandler("pow", function (value) {
  var v = num(value);
  if (v === null) return;
  state.power = Math.max(v, 0);
  state.lastMessageAt = Date.now();
  noteActivity(powerToRms(v));
  pushHistory(); // pow arrives at a steady ~10 Hz; it is the history clock
});

maxApi.addHandler("bpow", function (index, value) {
  var i = Math.floor(num(index) || 0);
  var v = num(value);
  if (v !== null && i >= 0 && i < state.bandPower.length) {
    state.bandPower[i] = Math.max(v, 0);
    state.lastMessageAt = Date.now();
  }
});

maxApi.addHandler("peak", function (channel, value) {
  var ch = Math.floor(num(channel) || 0);
  var v = num(value);
  if (v !== null && (ch === 0 || ch === 1)) {
    state.peak[ch] = v;
    state.lastMessageAt = Date.now();
    noteActivity(v);
  }
});

// v1 patch messages: count, NEVER interpret — a v1 'rms' carries a linear
// value, not the v2 power convention, and sqrt(v/2) would silently serve
// wrong numbers. legacy_msgs > 0 in ping means the DEVICE predates v2 and
// must be rebuilt; the Python side withholds levels when it sees this.
maxApi.addHandler("rms", function () {
  state.legacyMsgs += 1;
});
maxApi.addHandler("band", function () {
  state.legacyMsgs += 1;
});

function windowStats(now, windowMs) {
  var w = typeof windowMs === "number" ? windowMs : WINDOW_MS;
  var cutoff = now - w;
  var sum = 0;
  var count = 0;
  var rmsMax = 0;
  var peakMax = 0;
  var clipped = false;
  // History is time-ordered; walk from the newest end until the cutoff.
  for (var i = state.history.length - 1; i >= 0; i--) {
    var h = state.history[i];
    if (h.t < cutoff) break;
    sum += h.rms;
    count += 1;
    if (h.rms > rmsMax) rmsMax = h.rms;
    if (h.peak0 > peakMax) peakMax = h.peak0;
    if (h.peak1 > peakMax) peakMax = h.peak1;
    if (h.peak0 >= CLIP_LINEAR || h.peak1 >= CLIP_LINEAR) clipped = true;
  }
  if (count === 0) {
    return {
      rms_mean_db: DB_FLOOR,
      rms_max_db: DB_FLOOR,
      peak_max_db: DB_FLOOR,
      clipping: false,
      window_ms: w
    };
  }
  return {
    rms_mean_db: toDb(sum / count),
    rms_max_db: toDb(rmsMax),
    peak_max_db: toDb(peakMax),
    clipping: clipped, // latched: any clipped ~100ms frame within the window
    window_ms: w
  };
}

function snapshot(windowMs) {
  var now = Date.now();
  // Prune on READ, not only on message arrival: when DSP stops, the history
  // (and with it the clip latch) must drain within WINDOW_MS instead of
  // freezing forever — the v1 bug.
  pruneHistory(now);
  var age = state.lastMessageAt ? now - state.lastMessageAt : null;
  var stale = age === null || age > STALE_MS;
  var stats = windowStats(now, windowMs);
  var bandsDb = [];
  for (var i = 0; i < BAND_LABELS.length; i++) {
    bandsDb.push({
      hz: BAND_LABELS[i],
      level_db: stale ? DB_FLOOR : toDb(powerToRms(state.bandPower[i]))
    });
  }
  return {
    receiving_audio: !stale && now - state.lastNonZeroAt < SILENCE_MS,
    stale: stale, // true => DSP stopped/device bypassed; values are floored
    data_age_ms: age, // ms since the last measurement message; null = never fed
    rms_db: stale ? DB_FLOOR : toDb(powerToRms(state.power)),
    peak_db: stale ? [DB_FLOOR, DB_FLOOR] : [toDb(state.peak[0]), toDb(state.peak[1])],
    clipping: stats.clipping,
    bands: bandsDb,
    window_seconds: WINDOW_MS / 1000,
    window: stats,
    note:
      "Pre-master-fader. Stereo power metering (anti-phase safe); bands are " +
      "resonant octave filters 31 Hz-16 kHz — a meter, not a spectrum analyzer."
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
        bands: BAND_LABELS.length,
        uptime_seconds: Math.round((Date.now() - startedAt) / 1000),
        msgs_ok: state.msgsOk,
        msgs_bad: state.msgsBad,
        last_bad_sample: state.lastBadSample,
        legacy_msgs: state.legacyMsgs
      }
    };
  }
  if (type === "get_levels") {
    var w = WINDOW_MS;
    if (request && request.params && typeof request.params.window_ms === "number") {
      w = Math.max(100, Math.min(request.params.window_ms, WINDOW_MS));
    }
    return { status: "success", id: id, result: snapshot(w) };
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

// --- test feed (plain-Node only, and only when explicitly requested) --------
// Drives the recorded handlers with known values so cross-language tests can
// pin the sqrt(v/2) convention, band mapping, clip latching, and staleness.
// Inert in production: inside Max `insideMax` is true and the env is unset.
if (!insideMax && process.env.ABLETON_TAP_TEST_FEED) {
  var feedStart = Date.now();
  var feedStopMs = parseInt(process.env.ABLETON_TAP_TEST_FEED_STOP_MS || "0", 10);
  var feedTick = 0;
  var feeder = setInterval(function () {
    if (feedStopMs > 0 && Date.now() - feedStart >= feedStopMs) {
      clearInterval(feeder);
      return;
    }
    // rms 0.1 (-20.0 dB) => pow = 2 * 0.1² = 0.02 — pins the convention.
    testHandlers["pow"](0.02);
    for (var b = 0; b < BAND_LABELS.length; b++) {
      // band "1k" hot (-20 dB), all others -60 dB
      testHandlers["bpow"](b, b === 5 ? 0.02 : 0.000002);
    }
    // Clipped frames for the first ~300ms, then -6 dB: the latch must hold.
    testHandlers["peak"](0, feedTick < 3 ? 1.0 : 0.5);
    testHandlers["peak"](1, 0.5);
    feedTick += 1;
  }, 100);
}
