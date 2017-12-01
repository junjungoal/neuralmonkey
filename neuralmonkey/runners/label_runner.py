from typing import Any, List, Callable, Dict, Set, Optional
import numpy as np

from neuralmonkey.logging import log
from neuralmonkey.model.model_part import ModelPart
from neuralmonkey.vocabulary import Vocabulary, END_TOKEN_INDEX
from neuralmonkey.runners.base_runner import (
    BaseRunner, Executable, FeedDict, ExecutionResult, NextExecute)


class LabelRunExecutable(Executable):

    def __init__(self,
                 all_coders: Set[ModelPart],
                 fetches: FeedDict,
                 num_sessions: int,
                 vocabulary: Vocabulary,
                 postprocess: Optional[Callable]) -> None:
        self.all_coders = all_coders
        self._fetches = fetches
        self._num_sessions = num_sessions
        self._vocabulary = vocabulary
        self._postprocess = postprocess

        self.result = None  # type: Optional[ExecutionResult]

    def next_to_execute(self) -> NextExecute:
        """Get the feedables and tensors to run."""
        return (self.all_coders,
                self._fetches,
                None)

    def collect_results(self, results: List[Dict]) -> None:
        loss = results[0].get("loss", 0.)
        summed_logprobs = results[0]["label_logprobs"]
        input_mask = results[0]["input_mask"]

        for sess_result in results[1:]:
            loss += sess_result.get("loss", 0.)
            summed_logprobs = np.logaddexp(summed_logprobs,
                                           sess_result["label_logprobs"])
            assert input_mask == sess_result["input_mask"]

        argmaxes = np.argmax(summed_logprobs, axis=2)

        # CAUTION! FABULOUS HACK BELIEVE ME
        argmaxes -= END_TOKEN_INDEX
        argmaxes *= input_mask.astype(int)
        argmaxes += END_TOKEN_INDEX

        # must transpose argmaxes because vectors_to_sentences is time-major
        decoded_labels = self._vocabulary.vectors_to_sentences(argmaxes.T)

        if self._postprocess is not None:
            decoded_labels = self._postprocess(decoded_labels)

        self.result = ExecutionResult(
            outputs=decoded_labels,
            losses=[loss],
            scalar_summaries=None,
            histogram_summaries=None,
            image_summaries=None)

class LabelRunner(BaseRunner):

    def __init__(self,
                 output_series: str,
                 decoder: Any,
                 postprocess: Callable[[List[str]], List[str]] = None
                ) -> None:
        super(LabelRunner, self).__init__(output_series, decoder)
        self._postprocess = postprocess

        # make sure the lazy decoder creates its output tensor
        log("Decoder output tensor: {}".format(decoder.decoded))

    def get_executable(self,
                       compute_losses: bool = False,
                       summaries: bool = True,
                       num_sessions: int = 1) -> LabelRunExecutable:
        if compute_losses:
            if not hasattr(self._decoder, "cost"):
                raise TypeError("Decoder should have the 'cost' attribute")
            fetches = {"loss": getattr(self._decoder, "cost")}
        else:
            fetches = {}

        if not hasattr(self._decoder, "logprobs"):
            raise TypeError("Decoder should have the 'logprobs' attribute")

        if not hasattr(self._decoder, "encoder"):
            raise TypeError("Decoder should have the 'encoder' attribute")

        if not hasattr(self._decoder, "vocabulary"):
            raise TypeError("Decoder should have the 'vocabulary' attribute")

        fetches["label_logprobs"] = getattr(self._decoder, "logprobs")
        fetches["input_mask"] = getattr(self._decoder,
                                        "encoder").input_sequence.mask

        return LabelRunExecutable(self.all_coders,
                                  fetches,
                                  num_sessions,
                                  getattr(self._decoder, "vocabulary"),
                                  self._postprocess)

    @property
    def loss_names(self) -> List[str]:
        return ["loss"]
