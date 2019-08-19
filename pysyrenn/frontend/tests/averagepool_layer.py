"""Tests the methods in averagepool_layer.py
"""
import numpy as np
import torch
from helpers import main
from pysyrenn.frontend.strided_window_data import StridedWindowData
from pysyrenn.frontend.averagepool_layer import AveragePoolLayer

def test_compute():
    """Tests that the AveragePool layer correctly computes a AveragePool.
    """
    batch = 101
    width = 32
    height = 32
    channels = 3
    inputs = np.random.uniform(size=(101, height * width * channels))

    true_outputs = inputs.reshape((batch, height, width, channels))
    true_outputs = true_outputs.reshape((batch, height, width // 2, 2, channels))
    true_outputs = np.mean(true_outputs, axis=3)
    true_outputs = true_outputs.reshape((batch, height // 2, 2, -1, channels))
    true_outputs = np.mean(true_outputs, axis=2).reshape((batch, -1))

    window_data = StridedWindowData((height, width, channels),
                                    (2, 2), (2, 2), (0, 0), channels)
    averagepool_layer = AveragePoolLayer(window_data)
    assert np.allclose(averagepool_layer.compute(inputs), true_outputs)

    torch_inputs = torch.FloatTensor(inputs)
    torch_outputs = averagepool_layer.compute(torch_inputs).numpy()
    assert np.allclose(torch_outputs, true_outputs)

def test_serialize():
    """Tests that the layer correctly serializes/deserializes itself.
    """
    height, width, channels = np.random.choice([8, 16, 32, 64, 128], size=3)
    window_height, window_width = np.random.choice([2, 4, 8], size=2)

    window_data = StridedWindowData((height, width, channels),
                                    (window_height, window_width),
                                    (window_height, window_width),
                                    (0, 0), channels)

    serialized = AveragePoolLayer(window_data).serialize()
    assert serialized.WhichOneof("layer_data") == "averagepool_data"

    serialized_window_data = serialized.averagepool_data.window_data
    assert serialized_window_data == window_data.serialize()

    deserialized = AveragePoolLayer.deserialize(serialized)
    assert deserialized.serialize() == serialized

    serialized.relu_data.SetInParent()
    assert AveragePoolLayer.deserialize(serialized) is None

main(__name__, __file__)
