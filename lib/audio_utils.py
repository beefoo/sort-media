import array
import librosa
import math
from math_utils import weighted_mean
import numpy as np
import os
from pprint import pprint
from pydub import AudioSegment
from pysndfx import AudioEffectsChain
import re
import subprocess

def addFx(sound, effects, pad=3000, fade_in=100, fade_out=100):
    # Add padding
    if pad > 0:
        sound += AudioSegment.silent(duration=pad, frame_rate=sound.frame_rate)

    # convert pydub sound to np array
    samples = np.array(sound.get_array_of_samples())
    samples = samples.astype(np.int16)

    chain = AudioEffectsChain()
    for effect, value in effects:
        if effect == "reverb":
            chain.reverb(reverberance=value)
        elif effect == "distortion":
            chain.overdrive(gain=value)
        elif effect == "highpass":
            chain.highpass(value)
        elif effect == "lowpass":
            chain.lowpass(value)
        elif effect == "bass":
            chain.lowshelf(gain=value)
        elif effect == "echo":
            chain.custom("echo 0.8 0.9 %s 0.3" % value)

    # apply reverb effect
    fx = (chain)
    y = fx(samples)

    # convert it back to an array and create a new sound clip
    newData = array.array(sound.array_type, y)
    newSound = sound._spawn(newData)
    dur = len(newSound)
    newSound = newSound.fade_in(min(fade_in, dur)).fade_out(min(fade_out, dur))
    return newSound

def getAudioFile(fn, samplerate=44100):
    format = fn.split(".")[-1]
    # if this is an .mp4, convert to .mp3
    if format == "mp4":
        target = fn.replace(".mp4", ".mp3")
        if not os.path.isfile(target):
            command = ['ffmpeg',
                '-i', fn,
                '-ar', str(samplerate), # for defining sample rate
                '-q:a', '0', # for variable bitrate
                '-map', 'a', target]
            print(" ".join(command))
            finished = subprocess.check_call(command)
        fn = target
    return fn

def getAudioSamples(fn, min_dur=50, max_dur=-1, fft=2048, hop_length=512, backtrack=True, superFlux=True):
    basename = os.path.basename(fn)
    fn = getAudioFile(fn)

    # load audio
    y, sr = librosa.load(fn)
    y /= y.max()
    ylen = len(y)
    duration = int(round(ylen / sr * 1000))

    # retrieve onsets using superflux method
    # https://librosa.github.io/librosa/auto_examples/plot_superflux.html#sphx-glr-auto-examples-plot-superflux-py
    # http://dafx13.nuim.ie/papers/09.dafx2013_submission_12.pdf
    if superFlux:
        lag = 2
        n_mels = 138
        fmin = 27.5
        fmax = 16000.0
        max_size = 3
        S = librosa.feature.melspectrogram(y, sr=sr, n_fft=fft, hop_length=hop_length, fmin=fmin, fmax=fmax, n_mels=n_mels)
        odf = librosa.onset.onset_strength(S=librosa.power_to_db(S, ref=np.max), sr=sr, hop_length=hop_length, lag=lag, max_size=max_size)
        onsets = librosa.onset.onset_detect(onset_envelope=odf, sr=sr, hop_length=hop_length, backtrack=backtrack)

    # retrieve onsets using default method
    else:
        onsets = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop_length, backtrack=backtrack)

    times = [int(round(1.0 * hop_length * onset / sr * 1000)) for onset in onsets]
    # add the end of the audio
    times.append(duration-1)

    samples = []
    for i, t in enumerate(times):
        prev = times[i-1] if i > 0 else 0
        dur = t - prev
        if dur >= min_dur and (max_dur <= 0 or dur <= max_dur):
            samples.append({
                "filename": basename,
                "start": prev,
                "dur": dur
            })

    return samples

def getFeatures(y, sr, start, dur, fft=2048, hop_length=512):
    # analyze just the sample
    i0 = int(round(start / 1000.0 * sr))
    i1 = int(round((start+dur) / 1000.0 * sr))
    y = y[i0:i1]

    stft = librosa.feature.rmse(S=librosa.stft(y, n_fft=fft, hop_length=hop_length))[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(y=y)[0]

    power = round(weighted_mean(stft), 2)
    hz = round(weighted_mean(rolloff), 2)
    flatness = round(weighted_mean(flatness), 5)
    note = "-"

    if math.isinf(power):
        power = -1

    try:
        note = librosa.hz_to_note(hz)
    except OverflowError:
        hz = -1

    # parse note
    octave = -1
    matches = re.match("([A-Z]\#?b?)(\-?[0-9]+)", note)
    if matches:
        note = matches.group(1)
        octave = int(matches.group(2))

    return {
        "power": power,
        "hz": hz,
        "flatness": flatness,
        "note": note,
        "octave": octave
    }


# Taken from: https://github.com/ml4a/ml4a-guides/blob/master/notebooks/audio-tsne.ipynb
def getFeatureVector(y, sr, start, dur):

    # take at most one second
    dur = min(dur, 1000)

    # analyze just the sample
    i0 = int(round(start / 1000.0 * sr))
    i1 = int(round((start+dur) / 1000.0 * sr))
    y = y[i0:i1]

    S = librosa.feature.melspectrogram(y, sr=sr, n_mels=128)
    log_S = librosa.amplitude_to_db(S, ref=np.max)
    mfcc = librosa.feature.mfcc(S=log_S, n_mfcc=13)
    delta_mfcc = librosa.feature.delta(mfcc, mode='nearest')
    delta2_mfcc = librosa.feature.delta(mfcc, order=2, mode='nearest')
    feature_vector = np.concatenate((np.mean(mfcc,1), np.mean(delta_mfcc,1), np.mean(delta2_mfcc,1)))
    feature_vector = (feature_vector-np.mean(feature_vector))/np.std(feature_vector)
    return feature_vector

# Adapted from: https://github.com/paulnasca/paulstretch_python/blob/master/paulstretch_newmethod.py
def paulStretch(samplerate, smp, stretch, windowsize_seconds=0.25, onset_level=10.0):
    nchannels=smp.shape[0]

    def optimize_windowsize(n):
        orig_n=n
        while True:
            n=orig_n
            while (n%2)==0:
                n/=2
            while (n%3)==0:
                n/=3
            while (n%5)==0:
                n/=5

            if n<2:
                break
            orig_n+=1
        return orig_n

    #make sure that windowsize is even and larger than 16
    windowsize=int(windowsize_seconds*samplerate)
    if windowsize<16:
        windowsize=16
    windowsize=optimize_windowsize(windowsize)
    windowsize=int(windowsize/2)*2
    half_windowsize=int(windowsize/2)

    #correct the end of the smp
    nsamples=smp.shape[1]
    end_size=int(samplerate*0.05)
    if end_size<16:
        end_size=16

    smp[:,nsamples-end_size:nsamples]*=np.linspace(1,0,end_size)


    #compute the displacement inside the input file
    start_pos=0.0
    displace_pos=windowsize*0.5

    #create Hann window
    window=0.5-np.cos(np.arange(windowsize,dtype='float')*2.0*np.pi/(windowsize-1))*0.5

    old_windowed_buf=np.zeros((2,windowsize))
    hinv_sqrt2=(1+np.sqrt(0.5))*0.5
    hinv_buf=2.0*(hinv_sqrt2-(1.0-hinv_sqrt2)*np.cos(np.arange(half_windowsize,dtype='float')*2.0*np.pi/half_windowsize))/hinv_sqrt2

    freqs=np.zeros((2,half_windowsize+1))
    old_freqs=freqs

    num_bins_scaled_freq=32
    freqs_scaled=np.zeros(num_bins_scaled_freq)
    old_freqs_scaled=freqs_scaled

    displace_tick=0.0
    displace_tick_increase=1.0/stretch
    if displace_tick_increase>1.0:
        displace_tick_increase=1.0
    extra_onset_time_credit=0.0
    get_next_buf=True

    sdata = np.array([])
    while True:
        if get_next_buf:
            old_freqs=freqs
            old_freqs_scaled=freqs_scaled

            #get the windowed buffer
            istart_pos=int(np.floor(start_pos))
            buf=smp[:,istart_pos:istart_pos+windowsize]
            if buf.shape[1]<windowsize:
                buf=np.append(buf,np.zeros((2,windowsize-buf.shape[1])),1)
            buf=buf*window

            #get the amplitudes of the frequency components and discard the phases
            freqs=abs(np.fft.rfft(buf))

            #scale down the spectrum to detect onsets
            freqs_len=freqs.shape[1]
            if num_bins_scaled_freq<freqs_len:
                freqs_len_div=freqs_len//num_bins_scaled_freq
                new_freqs_len=freqs_len_div*num_bins_scaled_freq
                freqs_scaled=np.mean(np.mean(freqs,0)[:new_freqs_len].reshape([num_bins_scaled_freq,freqs_len_div]),1)
            else:
                freqs_scaled=np.zeros(num_bins_scaled_freq)


            #process onsets
            m=2.0*np.mean(freqs_scaled-old_freqs_scaled)/(np.mean(abs(old_freqs_scaled))+1e-3)
            if m<0.0:
                m=0.0
            if m>1.0:
                m=1.0
            # if plot_onsets:
            #     onsets.append(m)
            if m>onset_level:
                displace_tick=1.0
                extra_onset_time_credit+=1.0

        cfreqs=(freqs*displace_tick)+(old_freqs*(1.0-displace_tick))

        #randomize the phases by multiplication with a random complex number with modulus=1
        ph=np.random.uniform(0,2*np.pi,(nchannels,cfreqs.shape[1]))*1j
        cfreqs=cfreqs*np.exp(ph)

        #do the inverse FFT
        buf=np.fft.irfft(cfreqs)

        #window again the output buffer
        buf*=window

        #overlap-add the output
        output=buf[:,0:half_windowsize]+old_windowed_buf[:,half_windowsize:windowsize]
        old_windowed_buf=buf

        #remove the resulted amplitude modulation
        output*=hinv_buf

        #clamp the values to -1..1
        output[output>1.0]=1.0
        output[output<-1.0]=-1.0

        #write the output to wav file
        # outfile.writeframes(int16(output.ravel(1)*32767.0).tostring())
        sdata = np.append(sdata, output.ravel(1), axis=0)

        if get_next_buf:
            start_pos+=displace_pos

        get_next_buf=False

        if start_pos>=nsamples:
            # print ("100 %")
            break
        # sys.stdout.write("%d %% \r" % int(100.0*start_pos/nsamples))
        # sys.stdout.flush()

        if extra_onset_time_credit<=0.0:
            displace_tick+=displace_tick_increase
        else:
            credit_get=0.5*displace_tick_increase #this must be less than displace_tick_increase
            extra_onset_time_credit-=credit_get
            if extra_onset_time_credit<0:
                extra_onset_time_credit=0
            displace_tick+=displace_tick_increase-credit_get

        if displace_tick>=1.0:
            displace_tick=displace_tick % 1.0
            get_next_buf=True

    sdata = sdata * 32767.0
    sdata = sdata.astype(np.int16)
    return sdata

def stretchSound(sound, amount=2.0, fade_out=0.8):
    channels = sound.channels
    frame_rate = sound.frame_rate
    samples = np.array(sound.get_array_of_samples())
    samples = samples.astype(np.int16)
    samples = samples * (1.0/32768.0)
    if channels > 1:
        samples = samples.reshape(channels, len(samples)/channels, order='F')
    newData = paulStretch(frame_rate, samples, amount)
    newData = array.array(sound.array_type, newData)
    newSound = sound._spawn(newData)
    if fade_out > 0:
        fadeMs = int(round(len(newSound) * fade_out))
        newSound = newSound.fade_out(fadeMs)
    return newSound

def volumeToDb(volume):
    db = 0.0
    if volume < 1.0 or volume > 1.0:
        db = 10.0 * math.log(volume**2)
    return db
