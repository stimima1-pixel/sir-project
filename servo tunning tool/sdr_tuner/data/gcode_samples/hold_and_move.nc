;Hold and Move
;Position control demo. Needs a Motor Encoder or Servo Encoder configured in Feedback Settings, or every
;move below will be refused - Work Zone Count needs a real position source to mean anything.

G1010           (Capture: energize and hold at the current position)
G04 P1

G00 X1000       (move to 1000 counts)
G04 P1.5

G00 X5000       (move to 5000 counts)
G04 P1.5

G1020 U-2000    (move by -2000 counts, relative to wherever it is now)
G04 P1.5

G1000           (Reset All Axes)
