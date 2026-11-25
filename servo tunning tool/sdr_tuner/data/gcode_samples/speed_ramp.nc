(Program DEM1)

;This demo program changes speed of Axis 0 from 100 RPM to 3000 RPM and back in steps.
;Make sure Axis 0 is properly configured before launching this program.

G1030 X150	(Axis 0, Electronic Speed Control 150 RPM)
G04 P1.5	(Wait for 1.5 seconds)

G1030 X300	(Axis 0, Electronic Speed Control 300 RPM)
G04 P1.5	(Wait for 1.5 seconds)

G1030 [0]500	(Axis 0, Electronic Speed Control 500 RPM)
G04 P1.5	(Wait for 1.5 seconds)

G1030 X1000	(Axis 0, Electronic Speed Control 1000 RPM)
G04 P1.5	(Wait for 1.5 seconds)

G1030 X2000	(Axis 0, Electronic Speed Control 2000 RPM)
G04 P1.5	(Wait for 1.5 seconds)

G1030 X3000	(Axis 0, Electronic Speed Control 3000 RPM)
G04 P1.5	(Wait for 1.5 seconds)

G1030 X1000	(Axis 0, Electronic Speed Control 1000 RPM)
G04 P1.5	(Wait for 1.5 seconds)

G1030 X500	(Axis 0, Electronic Speed Control 500 RPM)
G04 P1.5	(Wait for 1.5 seconds)

G1000		(Reset All Axes)
