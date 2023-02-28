import os
import cv2 #pip install opencv-python
import pandas as pd
import numpy as np
import tensorflow as tf
import matplotlib.pyplot as plt
from os import listdir
from os.path import isfile, join
from PIL import Image
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Conv2D, MaxPooling2D, Flatten, Dropout, Dense


data = tf.keras.utils.image_dataset_from_directory(r'C:\Users\brunolopez\mldata\landscape_recognition\Landscape Classification\Landscape Classification Grouped')
data_iterator = data.as_numpy_iterator()
batch = data_iterator.next()

data = data.map(lambda x,y: (x/255, y))

train_size = int(len(data) * .7)
val_size = int(len(data) * .2)
test_size = int(len(data) * .1) + 1

train = data.take(train_size)
val = data.skip(train_size).take(val_size)
test = data.skip(train_size+val_size).take(test_size)

cnn = Sequential()

cnn.add(Conv2D(16, (3,3), 1, activation = 'relu', input_shape=(256,256,3)))
cnn.add(MaxPooling2D())

cnn.add(Conv2D(32, (3,3), 1, activation = 'relu'))
cnn.add(MaxPooling2D())

cnn.add(Conv2D(16, (3,3), 1, activation = 'relu'))
cnn.add(MaxPooling2D())
#flatten the model
cnn.add(Flatten())

cnn.add(Dense(256, activation = 'relu'))
cnn.add(Dense(5, activation='softmax'))

cnn.compile('adam', loss=tf.losses.SparseCategoricalCrossentropy(), metrics = ['accuracy'] )

cnn.summary()

logdir = r'C:\Users\brunolopez\mldata\landscape_recognition\logs'

tensor_callback = tf.keras.callbacks.TensorBoard(log_dir = logdir)

hist = cnn.fit(train, epochs = 40, validation_data=val, callbacks = [tensor_callback])