masks is applied to the pulses. Then the SHG spectrum for every phase mask is measured to obtain a two-dimensional measurement trace. The most prominent technique in this class is called multiphoton intrapulse interference phase scan (MIIPS) [26,27], where sinusoidal phase patterns with varying shifts are applied. Typically, this method is used for iterative spectral phase compensation by using an algorithm that extracts an approximation to the second derivative of the spectral phase from the measurement. Other techniques represent adaptations of existing measurement schemes to the use with a pulse shaper [28–31].

Time-domain ptychography (TDP) is a recently developed pulse measurement technique  $[32–34]$ . It is inspired by a coherent diffractive imaging technique of the same name  $[35]$ . It uses a correlation setup similar to FROG where the pulse in one arm is spectrally filtered. The pulse retrieval algorithms used for TDP are an adaption of the image retrieval algorithms in spatial ptychography  $[36,37]$ . They have also been successfully applied to cross-correlation FROG (XFROG)  $[38]$  and SHG-FROG  $[39,40]$ .

Typically, each family of these pulse measurement methods comprises both a specific experimental setup and a tailored retrieval algorithm, which makes them difficult to compare. They are, however, structurally similar. For that reason, our paper starts in Section 2 by developing a common formalism for what we call parameterized nonlinear process spectra (PNPS) measurements. It allows to describe most self-referenced techniques for ultrashort pulse measurement using the same formalism. It forms the basis of our work and is used to develop all arguments in the following sections.

The main idea of our paper is presented in Section 3. We discuss PNPS pulse retrieval as a nonlinear least squares problem. We propose this as the natural way to view the pulse retrieval problem and stress that the least squares solution is ideal under the assumption of Gaussian noise.

The main result of our paper is the common pulse retrieval algorithm (COPRA) described in Section 4. It can be applied universally to all PNPS measurements. In our tests, we found that it is more accurate than other specialized pulse retrieval algorithms in the presence of Gaussian measurement noise. Furthermore, we found it to be faster than general least squares solvers, and, thus, it enables fast retrieval of pulse amplitude and phase from PNPS measurements for which no specialized algorithms were published yet, such as SD-d-scan or MIIPS.

In Sections 5 and 6, we describe the comprehensive numerical tests that were performed to verify and demonstrate the properties of COPRA. They included testing the retrieval for various PNPS measurements, test pulses, and levels of noise. For SHG-FROG, we compare it to PCGPA and ptychographic retrieval. We show numerically that those algorithms do not converge on a least squares solution and, consequently, are less accurate in the presence of Gaussian measurement noise. We also demonstrate that COPRA is able to retrieve pulses from incomplete traces. Furthermore, we evaluate the applicability of general minimization algorithms on the pulse retrieval problem. We find that gradient-based algorithms such as Levenberg–Marquadt (LM) are generally superior to gradient-free methods.

In Section 7, we summarize the results and give an outlook on future work. In the Supplement 1, we also give more details and technical aspects that facilitate the application and reimplementation of COPRA.

## 2. CONCEPTS

In this section, we introduce a unified description of most self-referenced pulse measurement methods. It is based on the observation that the measured quantity is the same for all methods mentioned in the introduction: a set of pulse spectra after a nonlinear process. Specifically, the nonlinear process is tunable by some parameter that forms the second measurement dimension. For example, for SHG-FROG, the parameter is the pulse delay, and the nonlinear process a noncollinear SHG. For SHG-d-scan, the parameter is the insertion distance of a glass wedge, and the nonlinear process is a collinear SHG.

We call these measurements PNPS measurements. Other pulse measurement techniques exist that cannot be described in this way. Most prominently, this pertains to spectral phase interferometry for direct electric field reconstruction (SPIDER) [41] and other methods based on spectral interferometry. They do not require an iterative retrieval algorithm and are not subjects of this paper.

## A. Continuous PNPS Formalism

We work with the complex-valued pulse envelope $E(t)$ and its spectral counterpart $\tilde{E}(\omega)$, where $\omega = \Omega - \Omega_0$ is the centered frequency and $\Omega_0$ the central frequency. Both are related by the Fourier transform and its inverse using the following convention:

$$
\tilde {E} (\omega) = \mathcal {F} [ E ] (\omega) = \frac {1}{2 \pi} \int_ {- \infty} ^ {\infty} E (t) \mathrm{e} ^ {\mathrm{i} \omega t} \mathrm{d} t,\tag{1}
$$

$$
E (t) = \mathcal {F} ^ {- 1} [ \tilde {E} ] (t) = \int_ {- \infty} ^ {\infty} \tilde {E} (\omega) \mathrm{e} ^ {- \mathrm{i} t \omega} \mathrm{d} \omega .\tag{2}
$$

All PNPS traces $\tilde{T}$ can be modeled by the following equation:

$$
\tilde {T} (\delta , \omega ; \tilde {E}) = | \mathcal {F} \{\mathcal {S} _ {\delta} [ \tilde {E} ] (t) \} (\omega) | ^ {2}.\tag{3}
$$

$\tilde{T}$ depends on the pulse $\tilde{E}$ and is evaluated at the frequency $\omega$ and the parameter $\delta$. $S_{\delta}$ is the signal operator that describes a parameterized nonlinear process in the time domain. $\delta$ is a method-specific parameter that tunes the nonlinear process.

Depending on the structure of the signal operator, we distinguish between noncollinear methods (e.g., FROG or TDP) and collinear methods (e.g., d-scan or iFROG). Examples of the signal operator in the former case can be found in Table 1. In the latter case, we can decompose the signal operator in the following way:

$$
\mathcal {S} _ {\delta} [ \tilde {E} ] (t) = \mathcal {N} \{\mathcal {F} ^ {- 1} [ \tilde {H} _ {\delta} \tilde {E} ] \} (t).\tag{4}
$$

$\tilde{H}_{\delta}(\omega)$ is the parameterization filter and describes a parameterized linear operation in the frequency domain. Examples are listed in Table 2. N is the nonlinear process operator and describes the subsequent conversion of a pulse by a collinear nonlinear process.

Table 1. Signal Operator for Selected Noncollinear Schemes $^{a}$

| Method | $\mathcal{S}_{\tau}[\tilde{E}]$ |
| --- | --- |
| SHG-FROG | $\mathcal{F}^{-1}[e^{i\tau\omega}\tilde{E}]\mathcal{F}^{-1}[\tilde{E}]$ |
| PG-FROG | $\|\mathcal{F}^{-1}[e^{i\tau\omega}\tilde{E}]\|^{2}\mathcal{F}^{-1}[\tilde{E}]$ |
| TDP$^{b}$ | $\mathcal{F}^{-1}[e^{i\tau\omega}\tilde{B}(\omega)\tilde{E}]\mathcal{F}^{-1}[\tilde{E}]$ |

$^{a}$ The pulse delay  $\tau$  is the parameter  $\delta$  in these methods. More examples can be found in the Supplement 1.

$^{b}\tilde{B}(\omega)$ describes the transmission of a bandpass filter used in the scheme.

Table 2. Parametrization Filter for Selected Collinear Schemes $^{a}$

| Scheme | Parameter δ | $\widetilde{H}_{\delta}(\omega)$ |
| --- | --- | --- |
| d-Scanb | Glass insertion z | exp[ik(ω + Ω0)z] |
| MIIPSc | Pattern shift δ | exp[iα cos(γω - δ)] |
| iFROG | Delay τ | 1/2 + exp[iτ(ω + Ω0)]/2 |

$^{a}$ More examples can be found in the Supplement 1.

$^{b}k(\Omega)$  depends on the material of the wedges and is usually defined by Sellmeier equations.

$^{c}\alpha$  and  $\gamma$  are free parameters of the method and have to be adapted to the measured pulses.

Table 3. Nonlinear Process Operators for Collinear Schemes

| Process | SHG | THG | SD |
| --- | --- | --- | --- |
| $\mathcal{N}[E]$ | $E^{2}$ | $E^{3}$ | $\|E\|^{2}E$ |

Expressions for the processes commonly used in pulse measurement can be found in Table 3.

PNPS traces do not uniquely define a pulse. For example, they are all ambiguous to the constant and linear phase of  $\tilde{E}(\omega)$ . Some methods (e.g., SHG-FROG and SHG-iFROG) leave the direction of time undetermined. Additionally, the relative phase of pulse components well-separated in frequency can be shown to be ambiguous, similar to how it was done for FROG [42]. Answering the underlying question if a PNPS trace is essentially unique, i.e., if it defines pulse amplitude and phase up to a set of known, so-called trivial ambiguities, is out of scope for this work. Even for the well-studied FROG method, it is still a topic of ongoing research [7,8,43]. We will take a pragmatic approach and test for nontrivial ambiguities by numerically retrieving pulses from a large number of synthetic measurements.

## B. Discrete PNPS Formalism

To perform pulse retrieval, we have to introduce a discrete version of the PNPS formalism. We define equidistant simulation grids with N points in time and frequency,

$$
t _ {n} \equiv t _ {0} + n \Delta t, \quad n = 0, \dots , N - 1,\tag{5}
$$

$$
\omega_ {n} \equiv \omega_ {0} + n \Delta \omega .\tag{6}
$$

We set  $E_{n} \equiv E(t_{n})$  and  $\tilde{E}_{n} \equiv \tilde{E}(\omega_{n})$ . We use  $\mathbf{E} \equiv (E_{0}, \ldots, E_{N-1})$  and  $\tilde{\mathbf{E}} \equiv (\tilde{E}_{0}, \ldots, \tilde{E}_{N-1})$  to denote the whole pulse. The Fourier transform is approximated by discrete evaluation of the integral and is denoted by

$$
\tilde {E} _ {n} = \mathrm{FT} _ {k \to n} (E _ {k}) \quad \text {and} \quad E _ {k} = \mathrm{FT} _ {n \to k} ^ {- 1} (\tilde {E} _ {n}).\tag{7}
$$

We have M spectra for the parameters  $\delta_{0},\ldots,\delta_{M-1}$ . There is no restriction on the number, spacing, or position of the tuning parameters  $\delta_{m}$ , e.g., as it is required by PCGPA for FROG. The discrete PNPS signal  $S_{mk}$  is defined by a discrete evaluation of the signal operator at  $\delta_{m}$  and  $t_{k}$ ,

$$
S _ {m k} \equiv S _ {m k} (\tilde {\mathbf {E}}) \approx \mathcal {S} _ {\delta_ {m}} [ \tilde {E} ] (t _ {k}), \qquad \begin{array}{l} m = 0, \dots , M - 1 \\ k = 0, \dots , N - 1 \end{array} .\tag{8}
$$

Its counterpart in the frequency domain is denoted by

$$
\begin{array}{r l r}\tilde {S} _ {m n} \equiv \tilde {S} _ {m n} (\tilde {\mathbf {E}}) = \mathrm{FT} _ {k \rightarrow n} (S _ {m k}),&m = 0, \dots , M - 1\\&n = 0, \dots , N - 1.\end{array}\tag{9}
$$

Finally, we can calculate the discrete PNPS trace $\tilde{T}_{mn}$ by

$$
\tilde {T} _ {m n} \equiv \tilde {T} _ {m n} (\tilde {\mathbf {E}}) = | \tilde {S} _ {m n} (\tilde {\mathbf {E}}) | ^ {2} \approx T (\delta_ {m}, \omega_ {n}; \tilde {E}).\tag{10}
$$

The measurement from which the pulse is reconstructed, the measured PNPS trace, is denoted by  $\tilde{T}_{mn}^{meas}$ . More details on how the calculations are performed can be found in the Supplement 1 (Section S2).

## 3. PULSE RETRIEVAL PROBLEM

The discrete pulse retrieval problem is to find the pulse  $\tilde{E}$  that gives rise to a PNPS trace  $\tilde{T}_{mn}$  that matches the measurement  $\tilde{T}_{mn}^{meas}$ . As  $\tilde{T}_{mn}^{meas}$  is subject to measurement errors; the retrieval will never be exact, and we need to choose a metric to select the best solution.

In this work, we view pulse retrieval as a nonlinear least squares problem. It is a nonlinear inverse problem, and solving such problems in the least squares sense is very well-established and understood [44]. Under the assumption of Gaussian measurement errors, the least squares solution represents an optimal choice, namely the maximum-likelihood estimate [45], and a Gaussian distribution is usually a good model for noise in spectrometric measurements [46].

To explain why we put so much emphasis on finding a least squares solution, we have to anticipate a key result from Section 6.B. We found that retrieval algorithms based on generalized projections or ptychography do not converge well onto a solution in the least squares sense. In our numerical tests, we found that they stagnate before reaching the close proximity of the least squares solution. In consequence, they underperform in the presence of additive Gaussian noise. This fact has been noticed several times in the literature  $[11,24,31,47]$ , but the relation to the missing least squares property was not reported so far.

With this in mind, we state the pulse retrieval problem as fitting the 2N independent variables in  $\tilde{E}$  (real and imaginary parts) to MN dependent variables in  $\tilde{T}_{mn}^{meas}$  by minimizing the sum of squared residuals r,

$$
r \equiv r (\tilde {\mathbf {E}}) = \sum_ {m, n} [ \tilde {T} _ {m n} ^ {\text {meas}} - \mu \tilde {T} _ {m n} (\tilde {\mathbf {E}}) ] ^ {2}.\tag{11}
$$

Throughout the paper, we use the trace error $R$ defined by

$$
R \equiv R (\tilde {\mathbf {E}}) = r ^ {1 / 2} / [ M N (\max _ {m, n} \tilde {T} _ {m n} ^ {\mathrm{meas}}) ^ {2} ] ^ {1 / 2}\tag{12}
$$

to assess the convergence. R is the normalized root mean square error (NRMSE) between  $\tilde{T}_{mn}^{meas}$  and  $\mu\tilde{T}_{mn}$ . The normalization facilitates the comparison of R between different measurements and PNPS schemes. In the FROG literature, the maximum of  $\tilde{T}_{mn}^{meas}$  is usually normalized to unity and, thus, R is then equivalent to the FROG error G.

The scaling factor  $\mu$  in Eq. (11) accounts for different scales of the measured and computed traces. Its value can be obtained for every  $\tilde{E}$  from an analytical solution by

$$
\mu = \sum_ {m, n} [ \tilde {T} _ {m n} ^ {\text {meas}} \tilde {T} _ {m n} (\tilde {\mathbf {E}}) ] / \sum_ {m, n} \tilde {T} _ {m n} (\tilde {\mathbf {E}}) ^ {2}.\tag{13}
$$