#!/usr/bin/env python
# coding=utf-8
from __future__ import division, print_function, unicode_literals

import math

import numpy as np
import six
from brainstorm.handlers._cpuop import _crop_images
from brainstorm.randomness import Seedable
from brainstorm.utils import IteratorValidationError


class DataIterator(Seedable):
    """Base class for Data Iterators.

    Attributes:
        data_shapes (dict[str, tuple[int]]):
            List of input names that this iterator provides.
        length (int | None):
            Number of iterations that this iterator will run.
    """

    def __init__(self, data_shapes, length):
        """
        Args:
            data_shapes (dict[str, tuple[int]]):
                List of input names that this iterator provides.
            length (int | None):
                Number of iterations that this iterator will run.
        """
        super(DataIterator, self).__init__()
        self.data_shapes = data_shapes
        self.length = length

    def __call__(self, handler):
        pass


class AddGaussianNoise(DataIterator):
    """
    Adds Gaussian noise to data generated by another iterator, which must
    provide named data items (such as Online, Minibatches, Undivided). Only
    Numpy data is supported,

    Supports usage of different means and standard deviations for different
    named data items.
    """

    def __init__(self, iter, std_dict, mean_dict=None):
        """
        Args:
            iter (DataIterator):
                Any DataIterator which iterates over data that noise should be
                added to.
            std_dict (dict[str, float]):
                Specifies the standard deviation of the noise that should be
                added for some of the named data items.
            mean_dict (Optional(dict[str, float])):
                Specifies the mean of the gaussian noise that should be
                added for some of the named data items.
                Defaults to None meaning all means are treated as 0.
        """
        DataIterator.__init__(self, iter.data_shapes, iter.length)
        mean_keys = set(mean_dict.keys()) if mean_dict is not None else set()
        std_keys = set(std_dict.keys())
        if mean_dict is not None and mean_keys != std_keys:
            raise IteratorValidationError(
                "means and standard deviations must be provided for the same "
                "data names. But {} != {}".format(mean_keys, std_keys))
        for key in std_keys:
            if key not in iter.data_shapes:
                raise IteratorValidationError(
                    "key {} is not present in iterator. Available keys: {"
                    "}".format(key, iter.data_shapes.keys()))

        self.mean_dict = {} if mean_dict is None else mean_dict
        self.std_dict = std_dict
        self.iter = iter

    def __call__(self, handler):
        for data in self.iter(handler):
            for key, std in self.std_dict.items():
                mean = self.mean_dict.get(key, 0.0)
                data[key] = data[key] + std * self.rnd.standard_normal(
                    data[key].shape) + mean
            yield data


class AddSaltNPepper(DataIterator):
    """
    Adds Salt&Pepper noise to data generated by another iterator, which must
    provide named data items (such as Online, Minibatches, Undivided). Only
    Numpy data is supported,

    Supports usage of different amounts and ratios of salt VS pepper for
    different named data items.
    """

    def __init__(self, iter, prob_dict, ratio_dict=None):
        """
        Args:
            iter (DataIterator):
                Any DataIterator which iterates over data that noise should be
                added to.
            prob_dict (dict[str, float]):
                Specifies the probability that an input is affected for some of
                the named data items. Omitted data items are treated as having
                an amount of 0.
            ratio_dict (Optional(dict[str, float])):
                Specifies the ratio of salt of all corrupted inputs.
                Defaults to None meaning the ratio is treated as 0.5.
        """
        DataIterator.__init__(self, iter.data_shapes, iter.length)
        ratio_keys = set() if ratio_dict is None else set(ratio_dict.keys())
        prob_keys = set(prob_dict.keys())
        if ratio_dict is not None and ratio_keys != prob_keys:
            raise IteratorValidationError(
                "probabilities and ratios must be provided for the "
                "same data names. But {} != {}".format(prob_keys, ratio_keys))
        for key in prob_keys:
            if key not in iter.data_shapes:
                raise IteratorValidationError(
                    "key {} is not present in iterator. Available keys: {"
                    "}".format(key, iter.data_shapes.keys()))

        self.ratio_dict = {} if ratio_dict is None else ratio_dict
        self.prob_dict = prob_dict
        self.iter = iter

    def __call__(self, handler):
        for data in self.iter(handler):
            for key, pr in self.prob_dict.items():
                ratio = self.ratio_dict.get(key, 0.5)
                d = data[key].copy()
                r = self.rnd.rand(*d.shape)
                d[r >= 1.0 - pr * ratio] = 1.0  # salt
                d[r <= pr * (1.0 - ratio)] = 0.0  # pepper
                data[key] = d
            yield data


class Flip(DataIterator):
    """
    Randomly flip images horizontally. Images are generated by another
    iterator, which must provide named data items (such as Online,
    Minibatches, Undivided). Only 5D numpy data in TNCHW format is supported.

    Defaults to flipping the 'default' named data item with a probability
    of 0.5. Note that the last dimension is flipped, which typically
    corresponds to flipping images horizontally.
    """

    def __init__(self, iter, prob_dict=None):
        """
        Args:
            iter (DataIterator):
                Any DataIterator which iterates over data to be flipped.
            prob_dict (dict[str, float]):
                Specifies the probability of flipping for some named
                data items.
        """
        Seedable.__init__(self)
        super(Flip, self).__init__(iter.data_shapes, iter.length)
        prob_dict = {'default': 0.5} if prob_dict is None else prob_dict
        for key in prob_dict.keys():
            if key not in iter.data_shapes:
                raise IteratorValidationError(
                    "key {} is not present in iterator. Available keys: {"
                    "}".format(key, iter.data_shapes.keys()))
            if prob_dict[key] > 1.0 or prob_dict[key] < 0.0:
                raise IteratorValidationError("Invalid probability")
            if len(iter.data_shapes[key]) != 5:
                raise IteratorValidationError("Only 5D data is supported")
        self.prob_dict = prob_dict
        self.iter = iter

    def __call__(self, handler):
        for data in self.iter(handler):
            for name in self.prob_dict.keys():
                if self.rnd.random_sample() < self.prob_dict[name]:
                    assert isinstance(data[name], np.ndarray)
                    data[name] = data[name][..., ::-1]
            yield data


class OneHot(DataIterator):

    """
    Convert data to one hot vectors, according to provided vocabulary sizes.
    If vocabulary size is not provided for some data item, it is yielded as is.

    Currently this iterator only supports 3D data where the last (right-most)
    dimension is sized 1.
    """

    def __init__(self, iter, vocab_size_dict):
        """
        Args:
            iter (DataIterator):
                DataIterator which iterates over the images to be padded.
            vocab_size_dict (dict[str, int]):
                Specifies the size of one hot vectors (the vocabulary size)
                for some named data items.
        """
        DataIterator.__init__(self, iter.data_shapes, iter.length)
        for key in vocab_size_dict.keys():
            if key not in iter.data_shapes:
                raise IteratorValidationError(
                    "key {} is not present in iterator. Available keys: {"
                    "}".format(key, iter.data_shapes.keys()))
            if not isinstance(vocab_size_dict[key], int):
                raise IteratorValidationError("Vocabulary size must be int")
            shape = iter.data_shapes[key]
            if not (shape[-1] == 1 and len(shape) == 3):
                raise IteratorValidationError("Only 3D data is supported")
        self.vocab_size_dict = vocab_size_dict
        self.iter = iter

    def __call__(self, handler, verbose=False):
        for data in self.iter(handler):
            for name in self.vocab_size_dict.keys():
                new_data = np.eye(self.vocab_size_dict[name], dtype=np.bool)[data[name]]
                new_data = np.reshape(new_data, (new_data.shape[0], new_data.shape[1], new_data.shape[3]))
                data[name] = new_data
                yield data


class Pad(DataIterator):
    """
    Pads images equally on all sides. Images are generated by another
    iterator, which must provide named data items (such as Online,
    Minibatches, Undivided). Only 5D Numpy data is supported.

    5D data corresponds to sequences of multi-channel images, which is the
    typical use case. Zero-padding is used unless specified otherwise.
    """

    def __init__(self, iter, size_dict, value_dict=None):
        """
        Args:
            iter (DataIterator):
                A DataIterator which iterates over the images to be padded.
            size_dict (dict[str, int]):
                Specifies the padding sizes for some named data items.
            value_dict (dict[str, int]):
                Specifies the pad values for some named data items.
        """
        super(Pad, self).__init__(iter.data_shapes, iter.length)
        if value_dict is not None:
            if set(size_dict.keys()) != set(value_dict.keys()):
                raise IteratorValidationError(
                    "padding sizes and values must be provided for the same "
                    "data names")
        for key in size_dict.keys():
            if key not in iter.data_shapes:
                raise IteratorValidationError(
                    "key {} is not present in iterator. Available keys: {"
                    "}".format(key, iter.data_shapes.keys()))
            if len(iter.data_shapes[key]) != 5:
                raise IteratorValidationError("Only 5D data is supported")
        self.value_dict = {} if value_dict is None else value_dict
        self.size_dict = size_dict
        self.iter = iter

    def __call__(self, handler):
        for data in self.iter(handler):
            for name in self.size_dict.keys():
                assert isinstance(data[name], np.ndarray)
                t, b, c, h, w = data[name].shape
                size = self.size_dict[name]
                val = self.value_dict.get(name, 0.0)
                new_data = val * np.ones((t, b, c, h + 2 * size, w + 2 * size))
                new_data[:, :, :, size: -size, size: -size] = data[name]
                data[name] = new_data
            yield data


class RandomCrop(DataIterator):
    """
    Randomly crops image data. Images are generated by another
    iterator, which must provide named data items (such as Online,
    Minibatches, Undivided). Only 5D Numpy data is supported.

    5D data corresponds to sequences of multi-channel images, which is the
    typical use case.
    """

    def __init__(self, iter, shape_dict):
        """
        Args:
            iter (DataIterator):
                A DataIterator which iterates over data to be cropped.
            shape_dict (dict[str, (int, int)]):
                Specifies the crop shapes for some named data items.
        """
        super(RandomCrop, self).__init__(iter.data_shapes, iter.length)
        for key, val in shape_dict.items():
            if key not in iter.data_shapes:
                raise IteratorValidationError(
                    "key {} is not present in iterator. Available keys: {"
                    "}".format(key, iter.data_shapes.keys()))
            if not (isinstance(val, tuple) and len(val) == 2):
                raise IteratorValidationError("Shape must be a size 2 tuple")
            data_shape = iter.data_shapes[key]
            if len(data_shape) != 5:
                raise IteratorValidationError("Only 5D data is supported")
            if val[0] > data_shape[3] or val[0] < 0:
                raise IteratorValidationError("Invalid crop height")
            if val[1] > data_shape[4] or val[1] < 0:
                raise IteratorValidationError("Invalid crop width")
        self.shape_dict = shape_dict
        self.iter = iter

    def __call__(self, handler):
        for data in self.iter(handler):
            for name in self.shape_dict.keys():
                assert isinstance(data[name], np.ndarray)
                crop_h, crop_w = self.shape_dict[name]
                batch_size = data[name].shape[1]
                max_r = data[name].shape[3] - crop_h
                max_c = data[name].shape[4] - crop_w
                row_indices = self.rnd.random_integers(0, max_r, batch_size)
                col_indices = self.rnd.random_integers(0, max_c, batch_size)
                cropped = np.zeros(data[name].shape[:3] + (crop_h, crop_w))
                _crop_images(data[name], crop_h, crop_w, row_indices,
                             col_indices, cropped)
                data[name] = cropped
            yield data


class Undivided(DataIterator):
    """
    Processes the entire data in one block (only one iteration).
    """

    def __init__(self, **named_data):
        """
        Args:
            **named_data (dict[str, np.ndarray]):
                Named arrays with 3+ dimensions i.e. ('T', 'B', ...).
        """
        _assert_correct_data_format(named_data)
        data_shapes = {n: v.shape for n, v in named_data.items()}
        super(Undivided, self).__init__(data_shapes, 1)
        self.data = named_data
        self.total_size = int(sum(d.size for d in self.data.values()))

    def __call__(self, handler):
        yield self.data


class Minibatches(DataIterator):
    """
    Minibatch iterator for inputs and targets.

    If either a 'mask' is given or some other means of determining sequence
    length is specified by `cut_according_to`, this iterator also cuts the
    sequences in each minibatch to their maximum length (which can be less
    than the maximum length over the whole dataset).

    Note:
        When shuffling is enabled, this iterator only randomizes the order of
        minibatches, but doesn't re-shuffle instances across batches.
    """

    def __init__(self, batch_size=1, shuffle=True, cut_according_to='mask',
                 **named_data):
        """
        Args:
            batch_size (int):
            The number of data instances per batch. Defaults to 1.
            Brainstorm assumes that the second dimension (from the left) of
            the data indexes independent data items.
        shuffle (Optional[bool]):
            Flag indicating whether the order of batches should be randomized
            at the beginning of every pass through the data.
        cut_according_to (Optional[str or list or array]:
            Specify how to determine the length of the sequences for shortening
            them to the longest sequence of the current mini-batch.
            Defaults to 'mask' in which case it will determine the length of
            the sequences from the 'mask' named data entry. Can be any other
            data name, or a list where the i-th entry is an integer specifying
            the length of the i-th sequence.
        **named_data (dict[str, np.ndarray]):
            Named arrays with 3+ dimensions i.e. ('T', 'B', ...).
        """
        nr_sequences, time_steps = _assert_correct_data_format(named_data)
        data_shapes = {n: v.shape for n, v in named_data.items()}
        nr_batches = int(math.ceil(nr_sequences / batch_size))
        super(Minibatches, self).__init__(data_shapes, nr_batches)
        self.data = named_data
        self.shuffle = shuffle
        self.batch_size = batch_size
        if isinstance(cut_according_to, six.string_types):
            if cut_according_to in named_data:
                self.seq_lens = _calculate_lengths_from_mask(
                    named_data[cut_according_to])
            else:
                self.seq_lens = np.ones(nr_sequences, dtype=np.int) * time_steps
        else:
            self.seq_lens = np.array(cut_according_to)
            assert self.seq_lens.shape == (nr_sequences, )
        self.sample_size = int(
            sum(d.shape[0] * np.prod(d.shape[2:]) * batch_size
                for d in self.data.values()))

    def __call__(self, handler):
        indices = np.arange(self.length)
        if self.shuffle:
            self.rnd.shuffle(indices)
        for i, idx in enumerate(indices):
            batch_slice = slice(idx * self.batch_size,
                                (idx + 1) * self.batch_size)
            time_slice = slice(None, np.max(self.seq_lens[batch_slice]) )
            data = {k: v[time_slice, batch_slice]
                    for k, v in self.data.items()}
            yield data


def _assert_correct_data_format(named_data):
    nr_sequences = {}
    nr_timesteps = {}
    for name, data in named_data.items():
        if not hasattr(data, 'shape'):
            raise IteratorValidationError(
                "{} has a wrong type. (no shape attribute)".format(name)
            )
        if len(data.shape) < 3:
            raise IteratorValidationError(
                'All inputs have to have at least 3 dimensions, where the '
                'first two are time_size and batch_size.')
        nr_sequences[name] = data.shape[1]
        nr_timesteps[name] = data.shape[0]

    if min(nr_sequences.values()) != max(nr_sequences.values()):
        raise IteratorValidationError(
            'The number of sequences of all inputs must be equal, but got {}'
            .format(nr_sequences))
    if min(nr_timesteps.values()) != max(nr_timesteps.values()):
        raise IteratorValidationError(
            'The number of time steps of all inputs must be equal, '
            'but got {}'.format(nr_timesteps))

    return int(min(nr_sequences.values())), min(nr_timesteps.values())


def _calculate_lengths_from_mask(mask):
    assert mask.shape[2:] == (1,)
    b = mask[:, :, 0] != 0
    lengths = mask.shape[0] - b[::-1].argmax(axis=0)
    lengths[b.max(axis=0) == 0] = 0
    return lengths
