{
 "patcher": {
  "fileversion": 1,
  "appversion": {
   "major": 8,
   "minor": 5,
   "revision": 0,
   "architecture": "x64",
   "modernui": 1
  },
  "classnamespace": "box",
  "rect": [
   100.0,
   100.0,
   1300.0,
   780.0
  ],
  "bglocked": 0,
  "openinpresentation": 0,
  "default_fontsize": 12.0,
  "default_fontface": 0,
  "default_fontname": "Arial",
  "gridonopen": 1,
  "gridsize": [
   15.0,
   15.0
  ],
  "gridsnaponopen": 1,
  "objectsnaponopen": 1,
  "statusbarvisible": 2,
  "toolbarvisible": 1,
  "boxes": [
   {
    "box": {
     "id": "obj-100",
     "maxclass": "comment",
     "text": "AbletonMCP Tap v2 \u2014 stereo-power metering (anti-phase safe), 10 octave bands 31 Hz\u201316 kHz. Serves 127.0.0.1:9878. Build: m4l/README-lab.md",
     "numinlets": 1,
     "numoutlets": 0,
     "patching_rect": [
      20.0,
      10.0,
      760.0,
      20.0
     ]
    }
   },
   {
    "box": {
     "id": "obj-1",
     "maxclass": "newobj",
     "text": "plugin~",
     "numinlets": 2,
     "numoutlets": 2,
     "patching_rect": [
      20.0,
      50.0,
      60.0,
      22.0
     ],
     "outlettype": [
      "signal",
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-2",
     "maxclass": "newobj",
     "text": "plugout~",
     "numinlets": 2,
     "numoutlets": 2,
     "patching_rect": [
      20.0,
      700.0,
      66.0,
      22.0
     ],
     "outlettype": [
      "signal",
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-sqL",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      160.0,
      110.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-sqR",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      230.0,
      110.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-psum",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      160.0,
      150.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-pavg",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      160.0,
      185.0,
      140.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-psnap",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      160.0,
      220.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-pmsg",
     "maxclass": "message",
     "text": "pow $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      160.0,
      255.0,
      60.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-9",
     "maxclass": "newobj",
     "text": "peakamp~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      450.0,
      110.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-10",
     "maxclass": "message",
     "text": "peak 0 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      450.0,
      145.0,
      76.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-11",
     "maxclass": "newobj",
     "text": "peakamp~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      560.0,
      110.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-12",
     "maxclass": "message",
     "text": "peak 1 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      560.0,
      145.0,
      76.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-fL",
     "maxclass": "newobj",
     "text": "fffb~ 10 31.25 2. 1.414",
     "numinlets": 1,
     "numoutlets": 10,
     "patching_rect": [
      20.0,
      320.0,
      150.0,
      22.0
     ],
     "outlettype": [
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-fR",
     "maxclass": "newobj",
     "text": "fffb~ 10 31.25 2. 1.414",
     "numinlets": 1,
     "numoutlets": 10,
     "patching_rect": [
      190.0,
      320.0,
      150.0,
      22.0
     ],
     "outlettype": [
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal",
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-20",
     "maxclass": "newobj",
     "text": "node.script tap_server.js @autostart 1 @watch 0",
     "numinlets": 1,
     "numoutlets": 2,
     "patching_rect": [
      300.0,
      570.0,
      290.0,
      22.0
     ],
     "outlettype": [
      "",
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-21",
     "maxclass": "newobj",
     "text": "route status",
     "numinlets": 1,
     "numoutlets": 2,
     "patching_rect": [
      300.0,
      605.0,
      76.0,
      22.0
     ],
     "outlettype": [
      "",
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-22",
     "maxclass": "newobj",
     "text": "prepend set",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      300.0,
      640.0,
      74.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-23",
     "maxclass": "message",
     "text": "STARTING",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      300.0,
      675.0,
      120.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL0",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      20.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR0",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      78.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp0",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      20.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba0",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      20.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs0",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      20.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm0",
     "maxclass": "message",
     "text": "bpow 0 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      20.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL1",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      142.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR1",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      200.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp1",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      142.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba1",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      142.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs1",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      142.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm1",
     "maxclass": "message",
     "text": "bpow 1 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      142.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL2",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      264.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR2",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      322.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp2",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      264.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba2",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      264.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs2",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      264.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm2",
     "maxclass": "message",
     "text": "bpow 2 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      264.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL3",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      386.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR3",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      444.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp3",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      386.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba3",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      386.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs3",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      386.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm3",
     "maxclass": "message",
     "text": "bpow 3 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      386.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL4",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      508.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR4",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      566.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp4",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      508.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba4",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      508.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs4",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      508.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm4",
     "maxclass": "message",
     "text": "bpow 4 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      508.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL5",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      630.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR5",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      688.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp5",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      630.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba5",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      630.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs5",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      630.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm5",
     "maxclass": "message",
     "text": "bpow 5 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      630.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL6",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      752.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR6",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      810.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp6",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      752.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba6",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      752.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs6",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      752.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm6",
     "maxclass": "message",
     "text": "bpow 6 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      752.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL7",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      874.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR7",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      932.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp7",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      874.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba7",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      874.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs7",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      874.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm7",
     "maxclass": "message",
     "text": "bpow 7 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      874.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL8",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      996.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR8",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      1054.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp8",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      996.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba8",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      996.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs8",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      996.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm8",
     "maxclass": "message",
     "text": "bpow 8 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      996.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-bL9",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      1118.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bR9",
     "maxclass": "newobj",
     "text": "*~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      1176.0,
      380.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bp9",
     "maxclass": "newobj",
     "text": "+~",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      1118.0,
      415.0,
      34.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-ba9",
     "maxclass": "newobj",
     "text": "average~ 14400 bipolar",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      1118.0,
      450.0,
      118.0,
      22.0
     ],
     "outlettype": [
      "signal"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bs9",
     "maxclass": "newobj",
     "text": "snapshot~ 100",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      1118.0,
      485.0,
      90.0,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-bm9",
     "maxclass": "message",
     "text": "bpow 9 $1",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      1118.0,
      520.0,
      80.0,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   }
  ],
  "lines": [
   {
    "patchline": {
     "source": [
      "obj-1",
      0
     ],
     "destination": [
      "obj-2",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      1
     ],
     "destination": [
      "obj-2",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      0
     ],
     "destination": [
      "obj-sqL",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      0
     ],
     "destination": [
      "obj-sqL",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      1
     ],
     "destination": [
      "obj-sqR",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      1
     ],
     "destination": [
      "obj-sqR",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-sqL",
      0
     ],
     "destination": [
      "obj-psum",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-sqR",
      0
     ],
     "destination": [
      "obj-psum",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-psum",
      0
     ],
     "destination": [
      "obj-pavg",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-pavg",
      0
     ],
     "destination": [
      "obj-psnap",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-psnap",
      0
     ],
     "destination": [
      "obj-pmsg",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-pmsg",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      0
     ],
     "destination": [
      "obj-9",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-9",
      0
     ],
     "destination": [
      "obj-10",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-10",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      1
     ],
     "destination": [
      "obj-11",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-11",
      0
     ],
     "destination": [
      "obj-12",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-12",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      0
     ],
     "destination": [
      "obj-fL",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-1",
      1
     ],
     "destination": [
      "obj-fR",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      0
     ],
     "destination": [
      "obj-bL0",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      0
     ],
     "destination": [
      "obj-bL0",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      0
     ],
     "destination": [
      "obj-bR0",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      0
     ],
     "destination": [
      "obj-bR0",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL0",
      0
     ],
     "destination": [
      "obj-bp0",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR0",
      0
     ],
     "destination": [
      "obj-bp0",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp0",
      0
     ],
     "destination": [
      "obj-ba0",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba0",
      0
     ],
     "destination": [
      "obj-bs0",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs0",
      0
     ],
     "destination": [
      "obj-bm0",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm0",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      1
     ],
     "destination": [
      "obj-bL1",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      1
     ],
     "destination": [
      "obj-bL1",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      1
     ],
     "destination": [
      "obj-bR1",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      1
     ],
     "destination": [
      "obj-bR1",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL1",
      0
     ],
     "destination": [
      "obj-bp1",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR1",
      0
     ],
     "destination": [
      "obj-bp1",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp1",
      0
     ],
     "destination": [
      "obj-ba1",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba1",
      0
     ],
     "destination": [
      "obj-bs1",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs1",
      0
     ],
     "destination": [
      "obj-bm1",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm1",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      2
     ],
     "destination": [
      "obj-bL2",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      2
     ],
     "destination": [
      "obj-bL2",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      2
     ],
     "destination": [
      "obj-bR2",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      2
     ],
     "destination": [
      "obj-bR2",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL2",
      0
     ],
     "destination": [
      "obj-bp2",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR2",
      0
     ],
     "destination": [
      "obj-bp2",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp2",
      0
     ],
     "destination": [
      "obj-ba2",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba2",
      0
     ],
     "destination": [
      "obj-bs2",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs2",
      0
     ],
     "destination": [
      "obj-bm2",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm2",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      3
     ],
     "destination": [
      "obj-bL3",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      3
     ],
     "destination": [
      "obj-bL3",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      3
     ],
     "destination": [
      "obj-bR3",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      3
     ],
     "destination": [
      "obj-bR3",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL3",
      0
     ],
     "destination": [
      "obj-bp3",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR3",
      0
     ],
     "destination": [
      "obj-bp3",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp3",
      0
     ],
     "destination": [
      "obj-ba3",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba3",
      0
     ],
     "destination": [
      "obj-bs3",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs3",
      0
     ],
     "destination": [
      "obj-bm3",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm3",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      4
     ],
     "destination": [
      "obj-bL4",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      4
     ],
     "destination": [
      "obj-bL4",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      4
     ],
     "destination": [
      "obj-bR4",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      4
     ],
     "destination": [
      "obj-bR4",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL4",
      0
     ],
     "destination": [
      "obj-bp4",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR4",
      0
     ],
     "destination": [
      "obj-bp4",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp4",
      0
     ],
     "destination": [
      "obj-ba4",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba4",
      0
     ],
     "destination": [
      "obj-bs4",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs4",
      0
     ],
     "destination": [
      "obj-bm4",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm4",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      5
     ],
     "destination": [
      "obj-bL5",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      5
     ],
     "destination": [
      "obj-bL5",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      5
     ],
     "destination": [
      "obj-bR5",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      5
     ],
     "destination": [
      "obj-bR5",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL5",
      0
     ],
     "destination": [
      "obj-bp5",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR5",
      0
     ],
     "destination": [
      "obj-bp5",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp5",
      0
     ],
     "destination": [
      "obj-ba5",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba5",
      0
     ],
     "destination": [
      "obj-bs5",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs5",
      0
     ],
     "destination": [
      "obj-bm5",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm5",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      6
     ],
     "destination": [
      "obj-bL6",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      6
     ],
     "destination": [
      "obj-bL6",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      6
     ],
     "destination": [
      "obj-bR6",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      6
     ],
     "destination": [
      "obj-bR6",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL6",
      0
     ],
     "destination": [
      "obj-bp6",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR6",
      0
     ],
     "destination": [
      "obj-bp6",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp6",
      0
     ],
     "destination": [
      "obj-ba6",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba6",
      0
     ],
     "destination": [
      "obj-bs6",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs6",
      0
     ],
     "destination": [
      "obj-bm6",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm6",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      7
     ],
     "destination": [
      "obj-bL7",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      7
     ],
     "destination": [
      "obj-bL7",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      7
     ],
     "destination": [
      "obj-bR7",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      7
     ],
     "destination": [
      "obj-bR7",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL7",
      0
     ],
     "destination": [
      "obj-bp7",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR7",
      0
     ],
     "destination": [
      "obj-bp7",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp7",
      0
     ],
     "destination": [
      "obj-ba7",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba7",
      0
     ],
     "destination": [
      "obj-bs7",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs7",
      0
     ],
     "destination": [
      "obj-bm7",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm7",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      8
     ],
     "destination": [
      "obj-bL8",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      8
     ],
     "destination": [
      "obj-bL8",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      8
     ],
     "destination": [
      "obj-bR8",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      8
     ],
     "destination": [
      "obj-bR8",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL8",
      0
     ],
     "destination": [
      "obj-bp8",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR8",
      0
     ],
     "destination": [
      "obj-bp8",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp8",
      0
     ],
     "destination": [
      "obj-ba8",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba8",
      0
     ],
     "destination": [
      "obj-bs8",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs8",
      0
     ],
     "destination": [
      "obj-bm8",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm8",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      9
     ],
     "destination": [
      "obj-bL9",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fL",
      9
     ],
     "destination": [
      "obj-bL9",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      9
     ],
     "destination": [
      "obj-bR9",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-fR",
      9
     ],
     "destination": [
      "obj-bR9",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bL9",
      0
     ],
     "destination": [
      "obj-bp9",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bR9",
      0
     ],
     "destination": [
      "obj-bp9",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bp9",
      0
     ],
     "destination": [
      "obj-ba9",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-ba9",
      0
     ],
     "destination": [
      "obj-bs9",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bs9",
      0
     ],
     "destination": [
      "obj-bm9",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-bm9",
      0
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-20",
      0
     ],
     "destination": [
      "obj-21",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-21",
      0
     ],
     "destination": [
      "obj-22",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-22",
      0
     ],
     "destination": [
      "obj-23",
      1
     ]
    }
   }
  ]
 }
}
