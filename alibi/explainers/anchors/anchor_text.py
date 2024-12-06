import copy
import logging
import string
from copy import deepcopy
from typing import (TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union)

import numpy as np
import spacy

from alibi.utils.missing_optional_dependency import import_optional
from alibi.api.defaults import DEFAULT_DATA_ANCHOR, DEFAULT_META_ANCHOR
from alibi.api.interfaces import Explainer, Explanation
from alibi.exceptions import (PredictorCallError,
                              PredictorReturnTypeError)

from alibi.utils.wrappers import ArgmaxTransformer
from .anchor_base import AnchorBaseBeam
from .anchor_explanation import AnchorExplanation

from .text_samplers import UnknownSampler, SimilaritySampler, load_spacy_lexeme_prob

LanguageModelSampler = import_optional(
    'alibi.explainers.anchors.language_model_text_sampler',
    names=['LanguageModelSampler'])

if TYPE_CHECKING:
    import spacy  # noqa: F811
    from alibi.utils.lang_model import LanguageModel
else:
    from alibi.utils import LanguageModel

logger = logging.getLogger(__name__)

DEFAULT_SAMPLING_UNKNOWN = {
    "sample_proba": 0.5
}
"""
Default perturbation options for ``'unknown'`` sampling

    - ``'sample_proba'`` : ``float`` - probability of a word to be masked.
"""

DEFAULT_SAMPLING_SIMILARITY = {
    "sample_proba": 0.5,
    "top_n": 100,
    "temperature": 1.0,
    "use_proba": False
}
"""
Default perturbation options for ``'similarity'`` sampling

    - ``'sample_proba'`` : ``float`` - probability of a word to be masked.

    - ``'top_n'`` : ``int`` - number of similar words to sample for perturbations.

    - ``'temperature'`` : ``float`` - sample weight hyper-parameter if `use_proba=True`.

    - ``'use_proba'`` : ``bool`` - whether to sample according to the words similarity.
"""

DEFAULT_SAMPLING_LANGUAGE_MODEL = {
    "filling": "parallel",
    "sample_proba": 0.5,
    "top_n": 100,
    "temperature": 1.0,
    "use_proba": False,
    "frac_mask_templates": 0.1,
    "batch_size_lm": 32,
    "punctuation": string.punctuation,
    "stopwords": [],
    "sample_punctuation": False,
}
"""
Default perturbation options for ``'language_model'`` sampling

    - ``'filling'`` : ``str`` - filling method for language models. Allowed values: ``'parallel'``, \
    ``'autoregressive'``. ``'parallel'`` method corresponds to a single forward pass through the language model. The \
    masked words are sampled independently, according to the selected probability distribution (see `top_n`, \
    `temperature`, `use_proba`). `autoregressive` method fills the words one at the time. This corresponds to \
    multiple forward passes through  the language model which is computationally expensive.

    - ``'sample_proba'`` : ``float`` - probability of a word to be masked.

    - ``'top_n'`` : ``int`` - number of similar words to sample for perturbations.

    - ``'temperature'`` : ``float`` - sample weight hyper-parameter if use_proba equals ``True``.

    - ``'use_proba'`` : ``bool`` - whether to sample according to the predicted words distribution. If set to \
    ``False``, the `top_n` words are sampled uniformly at random.

    - ``'frac_mask_template'`` : ``float`` - fraction from the number of samples of mask templates to be generated. \
    In each sampling call, will generate `int(frac_mask_templates * num_samples)` masking templates. \
    Lower fraction corresponds to lower computation time since the batch fed to the language model is smaller. \
    After the words' distributions is predicted for each mask, a total of `num_samples` will be generated by sampling \
    evenly from each template. Note that lower fraction might correspond to less diverse sample. A `sample_proba=1` \
    corresponds to masking each word. For this case only one masking template will be constructed. \
    A `filling='autoregressive'` will generate `num_samples` masking templates regardless of the value \
    of `frac_mask_templates`.

    - ``'batch_size_lm'`` : ``int`` - batch size used for the language model forward pass.

    - ``'punctuation'`` : ``str`` - string of punctuation not to be masked.

    - ``'stopwords'`` : ``List[str]`` - list of words not to be masked.

    - ``'sample_punctuation'`` : ``bool`` - whether to sample punctuation to fill the masked words. If ``False``, the \
    punctuation defined in `punctuation` will not be sampled.
"""


class AnchorText(Explainer):
    # sampling methods
    SAMPLING_UNKNOWN = 'unknown'  #: Unknown sampling strategy.
    SAMPLING_SIMILARITY = 'similarity'  #: Similarity sampling strategy.
    SAMPLING_LANGUAGE_MODEL = 'language_model'  #: Language model sampling strategy.

    # default params
    DEFAULTS: Dict[str, Dict] = {
        SAMPLING_UNKNOWN: DEFAULT_SAMPLING_UNKNOWN,
        SAMPLING_SIMILARITY: DEFAULT_SAMPLING_SIMILARITY,
        SAMPLING_LANGUAGE_MODEL: DEFAULT_SAMPLING_LANGUAGE_MODEL,
    }

    # class of samplers
    CLASS_SAMPLER = {
        SAMPLING_UNKNOWN: UnknownSampler,
        SAMPLING_SIMILARITY: SimilaritySampler,
        SAMPLING_LANGUAGE_MODEL: LanguageModelSampler
    }

    def __init__(self,
                 predictor: Callable[[List[str]], np.ndarray],
                 sampling_strategy: str = 'unknown',
                 nlp: Optional['spacy.language.Language'] = None,
                 language_model: Union['LanguageModel', None] = None,
                 seed: int = 0,
                 **kwargs: Any) -> None:
        """
        Initialize anchor text explainer.

        Parameters
        ----------
        predictor
            A callable that takes a list of text strings representing `N` data points as inputs and returns `N` outputs.
        sampling_strategy
            Perturbation distribution method:

             - ``'unknown'`` - replaces words with UNKs.

             - ``'similarity'`` - samples according to a similarity score with the corpus embeddings.

             - ``'language_model'`` - samples according the language model's output distributions.

        nlp
            `spaCy` object when sampling method is ``'unknown'`` or ``'similarity'``.
        language_model
            Transformers masked language model. This is a model that it adheres to the
            `LanguageModel` interface we define in :py:class:`alibi.utils.lang_model.LanguageModel`.
        seed
            If set, ensure identical random streams.
        kwargs
            Sampling arguments can be passed as `kwargs` depending on the `sampling_strategy`.
            Check default arguments defined in:

                - :py:data:`alibi.explainers.anchor_text.DEFAULT_SAMPLING_UNKNOWN`

                - :py:data:`alibi.explainers.anchor_text.DEFAULT_SAMPLING_SIMILARITY`

                - :py:data:`alibi.explainers.anchor_text.DEFAULT_SAMPLING_LANGUAGE_MODEL`

        Raises
        ------
        :py:class:`alibi.exceptions.PredictorCallError`
            If calling `predictor` fails at runtime.
        :py:class:`alibi.exceptions.PredictorReturnTypeError`
            If the return type of `predictor` is not `np.ndarray`.
        """
        super().__init__(meta=copy.deepcopy(DEFAULT_META_ANCHOR))
        self._seed(seed)

        # set the predictor
        self.predictor = self._transform_predictor(predictor)

        # define model which can be either spacy object or LanguageModel
        # the initialization of the model happens in _validate_kwargs
        self.model: Union['spacy.language.Language', LanguageModel]  #: Language model to be used.

        # validate kwargs
        self.perturb_opts, all_opts = self._validate_kwargs(sampling_strategy=sampling_strategy, nlp=nlp,
                                                            language_model=language_model, **kwargs)

        # set perturbation
        self.perturbation: Any = \
            self.CLASS_SAMPLER[self.sampling_strategy](self.model, self.perturb_opts)  #: Perturbation method.

        # update metadata
        self.meta['params'].update(seed=seed)
        self.meta['params'].update(**all_opts)

    def _validate_kwargs(self,
                         sampling_strategy: str,
                         nlp: Optional['spacy.language.Language'] = None,
                         language_model: Optional['LanguageModel'] = None,
                         **kwargs: Any) -> Tuple[dict, dict]:

        # set sampling method
        sampling_strategy = sampling_strategy.strip().lower()
        sampling_strategies = [
            self.SAMPLING_UNKNOWN,
            self.SAMPLING_SIMILARITY,
            self.SAMPLING_LANGUAGE_MODEL
        ]

        # validate sampling method
        if sampling_strategy not in sampling_strategies:
            sampling_strategy = self.SAMPLING_UNKNOWN
            logger.warning(f"Sampling method {sampling_strategy} if not valid. "
                           f"Using the default value `{self.SAMPLING_UNKNOWN}`")

        if sampling_strategy in [self.SAMPLING_UNKNOWN, self.SAMPLING_SIMILARITY]:
            if nlp is None:
                raise ValueError("spaCy model can not be `None` when "
                                 f"`sampling_strategy` set to `{sampling_strategy}`.")
            # set nlp object
            self.model = load_spacy_lexeme_prob(nlp)
        else:
            if language_model is None:
                raise ValueError("Language model can not be `None` when "
                                 f"`sampling_strategy` set to `{sampling_strategy}`")
            # set language model object
            self.model = language_model
            self.model_class = type(language_model).__name__

        # set sampling method
        self.sampling_strategy = sampling_strategy

        # get default args
        default_args: dict = self.DEFAULTS[self.sampling_strategy]
        perturb_opts: dict = deepcopy(default_args)  # contains only the perturbation params
        all_opts = deepcopy(default_args)  # contains params + some potential incorrect params

        # compute common keys
        allowed_keys = set(perturb_opts.keys())
        provided_keys = set(kwargs.keys())
        common_keys = allowed_keys & provided_keys

        # incorrect keys
        if len(common_keys) < len(provided_keys):
            incorrect_keys = ", ".join(provided_keys - common_keys)
            logger.warning("The following keys are incorrect: " + incorrect_keys)

        # update defaults args and all params
        perturb_opts.update({key: kwargs[key] for key in common_keys})
        all_opts.update(kwargs)
        return perturb_opts, all_opts

    def sampler(self, anchor: Tuple[int, tuple], num_samples: int, compute_labels: bool = True) -> \
            Union[List[Union[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, int]], List[np.ndarray]]:
        """
        Generate perturbed samples while maintaining features in positions specified in
        anchor unchanged.

        Parameters
        ----------
        anchor
             - ``int`` - the position of the anchor in the input batch.

             - ``tuple`` - the anchor itself, a list of words to be kept unchanged.

        num_samples
            Number of generated perturbed samples.
        compute_labels
            If ``True``, an array of comparisons between predictions on perturbed samples and
            instance to be explained is returned.

        Returns
        -------
        If ``compute_labels=True``, a list containing the following is returned

         - `covered_true` - perturbed examples where the anchor applies and the model prediction \
         on perturbation is the same as the instance prediction.

         - `covered_false` - perturbed examples where the anchor applies and the model prediction \
         is NOT the same as the instance prediction.

         - `labels` - num_samples ints indicating whether the prediction on the perturbed sample \
         matches (1) the label of the instance to be explained or not (0).

         - `data` - Matrix with 1s and 0s indicating whether a word in the text has been perturbed for each sample.

         - `-1.0` - indicates exact coverage is not computed for this algorithm.

         - `anchor[0]` - position of anchor in the batch request.

        Otherwise, a list containing the data matrix only is returned.
        """

        raw_data, data = self.perturbation(anchor[1], num_samples)

        # create labels using model predictions as true labels
        if compute_labels:
            labels = self.compare_labels(raw_data)
            covered_true = raw_data[labels][:self.n_covered_ex]
            covered_false = raw_data[np.logical_not(labels)][:self.n_covered_ex]

            # coverage set to -1.0 as we can't compute 'true' coverage for this model
            return [covered_true, covered_false, labels.astype(int), data, -1.0, anchor[0]]
        else:
            return [data]

    def compare_labels(self, samples: np.ndarray) -> np.ndarray:
        """
        Compute the agreement between a classifier prediction on an instance to be explained
        and the prediction on a set of samples which have a subset of features fixed to a
        given value (aka compute the precision of anchors).

        Parameters
        ----------
        samples
            Samples whose labels are to be compared with the instance label.

        Returns
        -------
        A `numpy` boolean array indicating whether the prediction was the same as the instance label.
        """
        return self.predictor(samples.tolist()) == self.instance_label

    def explain(self,  # type: ignore[override]
                text: str,
                threshold: float = 0.95,
                delta: float = 0.1,
                tau: float = 0.15,
                batch_size: int = 100,
                coverage_samples: int = 10000,
                beam_size: int = 1,
                stop_on_first: bool = True,
                max_anchor_size: Optional[int] = None,
                min_samples_start: int = 100,
                n_covered_ex: int = 10,
                binary_cache_size: int = 10000,
                cache_margin: int = 1000,
                verbose: bool = False,
                verbose_every: int = 1,
                **kwargs: Any) -> Explanation:
        """
        Explain instance and return anchor with metadata.

        Parameters
        ----------
        text
            Text instance to be explained.
        threshold
            Minimum anchor precision threshold. The algorithm tries to find an anchor that maximizes the coverage
            under precision constraint. The precision constraint is formally defined as
            :math:`P(prec(A) \\ge t) \\ge 1 - \\delta`, where :math:`A` is an anchor, :math:`t` is the `threshold`
            parameter, :math:`\\delta` is the `delta` parameter, and :math:`prec(\\cdot)` denotes the precision
            of an anchor. In other words, we are seeking for an anchor having its precision greater or equal than
            the given `threshold` with a confidence of `(1 - delta)`. A higher value guarantees that the anchors are
            faithful to the model, but also leads to more computation time. Note that there are cases in which the
            precision constraint cannot be satisfied due to the quantile-based discretisation of the numerical
            features. If that is the case, the best (i.e. highest coverage) non-eligible anchor is returned.
        delta
            Significance threshold. `1 - delta` represents the confidence threshold for the anchor precision
            (see `threshold`) and the selection of the best anchor candidate in each iteration (see `tau`).
        tau
            Multi-armed bandit parameter used to select candidate anchors in each iteration. The multi-armed bandit
            algorithm tries to find within a tolerance `tau` the most promising (i.e. according to the precision)
            `beam_size` candidate anchor(s) from a list of proposed anchors. Formally, when the `beam_size=1`,
            the multi-armed bandit algorithm seeks to find an anchor :math:`A` such that
            :math:`P(prec(A) \\ge prec(A^\\star) - \\tau) \\ge 1 - \\delta`, where :math:`A^\\star` is the anchor
            with the highest true precision (which we don't know), :math:`\\tau` is the `tau` parameter,
            :math:`\\delta` is the `delta` parameter, and :math:`prec(\\cdot)` denotes the precision of an anchor.
            In other words, in each iteration, the algorithm returns with a probability of at least `1 - delta` an
            anchor :math:`A` with a precision within an error tolerance of `tau` from the precision of the
            highest true precision anchor :math:`A^\\star`. A bigger value for `tau` means faster convergence but also
            looser anchor conditions.
        batch_size
            Batch size used for sampling. The Anchor algorithm will query the black-box model in batches of size
            `batch_size`. A larger `batch_size` gives more confidence in the anchor, again at the expense of
            computation time since it involves more model prediction calls.
        coverage_samples
            Number of samples used to estimate coverage from during anchor search.
        beam_size
            Number of candidate anchors selected by the multi-armed bandit algorithm in each iteration from a list of
            proposed anchors. A bigger beam  width can lead to a better overall anchor (i.e. prevents the algorithm
            of getting stuck in a local maximum) at the expense of more computation time.
        stop_on_first
            If ``True``, the beam search algorithm will return the first anchor that has satisfies the
            probability constraint.
        max_anchor_size
            Maximum number of features to include in an anchor.
        min_samples_start
            Number of samples used for anchor search initialisation.
        n_covered_ex
            How many examples where anchors apply to store for each anchor sampled during search
            (both examples where prediction on samples agrees/disagrees with predicted label are stored).
        binary_cache_size
            The anchor search pre-allocates `binary_cache_size` batches for storing the boolean arrays
            returned during sampling.
        cache_margin
            When only ``max(cache_margin, batch_size)`` positions in the binary cache remain empty, a new cache
            of the same size is pre-allocated to continue buffering samples.
        verbose
            Display updates during the anchor search iterations.
        verbose_every
            Frequency of displayed iterations during anchor search process.
        **kwargs
            Other keyword arguments passed to the anchor beam search and the text sampling and perturbation functions.

        Returns
        -------
        `Explanation` object containing the anchor explaining the instance with additional metadata as attributes. \
        Contains the following data-related attributes

         - `anchor` : ``List[str]`` - a list of words in the proposed anchor.

         - `precision` : ``float`` - the fraction of times the sampled instances where the anchor holds yields \
         the same prediction as the original instance. The precision will always be  threshold for a valid anchor.

         - `coverage` : ``float`` - the fraction of sampled instances the anchor applies to.
        """
        # get params for storage in meta
        params = locals()
        remove = ['text', 'self']
        for key in remove:
            params.pop(key)

        params = deepcopy(params)  # Get a reference to itself if not deepcopy for LM sampler

        # store n_covered_ex positive/negative examples for each anchor
        self.n_covered_ex = n_covered_ex
        self.instance_label = self.predictor([text])[0]

        # set sampler
        self.perturbation.set_text(text)

        # get anchors and add metadata
        mab = AnchorBaseBeam(
            samplers=[self.sampler],
            sample_cache_size=binary_cache_size,
            cache_margin=cache_margin,
            **kwargs
        )

        result: Any = mab.anchor_beam(
            delta=delta,
            epsilon=tau,
            batch_size=batch_size,
            desired_confidence=threshold,
            max_anchor_size=max_anchor_size,
            min_samples_start=min_samples_start,
            beam_size=beam_size,
            coverage_samples=coverage_samples,
            stop_on_first=stop_on_first,
            verbose=verbose,
            verbose_every=verbose_every,
            **kwargs,
        )

        if self.sampling_strategy == self.SAMPLING_LANGUAGE_MODEL:
            # take the whole word (this points just to the first part of the word)
            result['positions'] = [self.perturbation.ids_mapping[i] for i in result['feature']]
            result['names'] = [
                self.perturbation.model.select_word(
                    self.perturbation.head_tokens,
                    idx_feature,
                    self.perturbation.perturb_opts['punctuation']
                ) for idx_feature in result['positions']
            ]
        else:
            result['names'] = [self.perturbation.words[x] for x in result['feature']]
            result['positions'] = [self.perturbation.positions[x] for x in result['feature']]

        # set mab
        self.mab = mab
        return self._build_explanation(text, result, self.instance_label, params)

    def _build_explanation(self, text: str, result: dict, predicted_label: int, params: dict) -> Explanation:
        """
        Uses the metadata returned by the anchor search algorithm together with
        the instance to be explained to build an explanation object.

        Parameters
        ----------
        text
            Instance to be explained.
        result
            Dictionary containing the search result and metadata.
        predicted_label
            Label of the instance to be explained. Inferred if not received.
        params
            Arguments passed to `explain`.
        """

        result['instance'] = text
        result['instances'] = [text]  # TODO: should this be an array?
        result['prediction'] = np.array([predicted_label])
        exp = AnchorExplanation('text', result)

        # output explanation dictionary
        data = copy.deepcopy(DEFAULT_DATA_ANCHOR)
        data.update(anchor=exp.names(),
                    precision=exp.precision(),
                    coverage=exp.coverage(),
                    raw=exp.exp_map)

        # create explanation object
        explanation = Explanation(meta=copy.deepcopy(self.meta), data=data)

        # params passed to explain
        # explanation.meta['params'].update(params)
        return explanation

    def _transform_predictor(self, predictor: Callable) -> Callable:
        # check if predictor returns predicted class or prediction probabilities for each class
        # if needed adjust predictor so it returns the predicted class
        x = ['Hello world']
        try:
            prediction = predictor(x)
        except Exception as e:
            msg = f"Predictor failed to be called on x={x}. " \
                  f"Check that `predictor` works with inputs of type List[str]."
            raise PredictorCallError(msg) from e

        if not isinstance(prediction, np.ndarray):
            msg = f"Excepted predictor return type to be {np.ndarray} but got {type(prediction)}."
            raise PredictorReturnTypeError(msg)

        if np.argmax(prediction.shape) == 0:
            return predictor
        else:
            transformer = ArgmaxTransformer(predictor)
            return transformer

    def reset_predictor(self, predictor: Callable) -> None:
        """
        Resets the predictor function.

        Parameters
        ----------
        predictor
            New predictor function.
        """
        self.predictor = self._transform_predictor(predictor)

    def _seed(self, seed: int) -> None:
        np.random.seed(seed)
        # If LanguageModel is used, we need to set the seed for tf as well.
        if hasattr(self, 'model') and isinstance(self.model, LanguageModelSampler):
            self.perturbation.seed(seed)
