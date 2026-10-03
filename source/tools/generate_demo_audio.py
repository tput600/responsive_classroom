"""Generate the project's original 60-second rest cue; no sampled recordings.

Requires the optional build tool lameenc==1.8.1. Its library is not shipped.
The generated cue and this script are covered by the project MIT license.
"""
from array import array
import math
from pathlib import Path
import sys


def main():
    import lameenc
    rate, duration = 22050, 60
    chords = ((196., 246.94, 293.66), (220., 277.18, 329.63),
              (164.81, 207.65, 246.94), (174.61, 220., 261.63))
    samples = array('h')
    for index in range(rate * duration):
        t = index / rate
        phase = t % 15 / 15
        frequencies = chords[min(3, int(t / 15))]
        amplitude = math.sin(math.pi * phase) ** 2
        pulse = .40 + .60 * (.5 - .5 * math.cos(2 * math.pi * t / 3))
        value = sum(math.sin(2 * math.pi * f * t) for f in frequencies) / 3
        samples.append(round(value * amplitude * pulse * 10000))
    if sys.byteorder != 'little':
        samples.byteswap()
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(1)
    encoder.set_quality(2)
    destination = Path(__file__).resolve().parents[1] / 'resources/audio/default-rest.mp3'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoder.encode(samples.tobytes()) + encoder.flush())
    print(f'Created {destination.name}: {destination.stat().st_size} bytes')


if __name__ == '__main__':
    main()
