import tensorflow as tf
import os
import numpy as np
import time
import sys
from random import randint, sample
from collections import Counter, OrderedDict
from subprocess import call
import io
import scipy.io
import math
import matplotlib.pyplot as plt

onTpu = False
if 'COLAB_TPU_ADDR' in os.environ:
    onTpu = True
    tpu = os.environ['COLAB_TPU_ADDR']
    print(tpu)
    
if onTpu: # in Colab TPU
    from google.colab import drive
    from IPython.lib import backgroundjobs as bg
    from tensorflow.python.keras.layers import Input, LSTM, TimeDistributed, Dense, Bidirectional, GRU, Layer
    from tensorflow.python.keras.models import Sequential, load_model
    from tensorflow.python.keras.layers.core import Dropout
    from tensorflow.python.keras import initializers, optimizers, regularizers, constraints
    from tensorflow.python.keras import backend as K
    from tensorflow.python.keras.callbacks import TensorBoard, ModelCheckpoint, CSVLogger, Callback
    from tensorflow.python.keras.utils import to_categorical, multi_gpu_model
    baseDrive = '/content/drive/My Drive/data/capgMyo/'
    drive.mount('/content/drive')
else: # on GPU
    import os
    if "10.4.17.191" in os.environ["SSH_CONNECTION"]: # MH 8 GPU server / 7
        os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"
        os.environ["CUDA_VISIBLE_DEVICES"]="1,2,3,4,5,6,7"
    from keras.layers import Input, LSTM, TimeDistributed, Dense, Bidirectional, GRU, Layer
    from keras.models import Sequential, load_model
    from keras.layers.core import Dropout
    from keras import initializers, optimizers, regularizers, constraints
    from keras import backend as K
    from keras.callbacks import TensorBoard, ModelCheckpoint, CSVLogger, Callback
    from keras.utils import to_categorical, multi_gpu_model
    baseDrive = '/home/istvan/capgMyo/'

NUMBER_OF_FEATURES = 128
recurrent_dropout = 0.5
dropout = 0.5
number_of_classes = 12
cellNeurons = 512
denseNeurons = 512

def step_decay(epoch):
	initial_lrate = 0.001
	drop = 0.75
	epochs_drop = 20.0
	lrate = initial_lrate * math.pow(drop, math.floor((1+epoch)/epochs_drop))
	return lrate

def toTpuModel(model):
  # This address identifies the TPU we'll use when configuring TensorFlow.
  TPU_WORKER = 'grpc://' + tpu
  tf.logging.set_verbosity(tf.logging.INFO)

  tpu_model = tf.contrib.tpu.keras_to_tpu_model(
    model,
    strategy=tf.contrib.tpu.TPUDistributionStrategy(
      tf.contrib.cluster_resolver.TPUClusterResolver(TPU_WORKER)))

  #tpu_model.summary()
  
  return tpu_model

def toMultiGpuModel(model):
    try:
        gpu_model = multi_gpu_model(model)
        return gpu_model
    except:
        print("gpu_model error")
        return None

def download(db='b', num=20):
    for i in range(num):
        name = "http://zju-capg.org/myo/data/db"+db+"-preprocessed-" + '{:03d}'.format(i+1) + ".zip"
        call(["wget", name])

def unzip(db='b', num=20):
    for i in range(num):
        name = "db"+db+"-preprocessed-" + '{:03d}'.format(i+1) + ".zip"
        call(["unzip", name, "-d", "unzipped-db"+db])

def playground(db='b'):
    data = scipy.io.loadmat("unzipped-db"+db+"/020-005-008.mat")
    print(type(data))
    print (data.keys())
    # dict_keys(['__header__', '__version__', '__globals__', 'trial', 'data', 'gesture', 'subject'])
    #print (data['trial'])
    print (data['data'].shape)
    #print (data['gesture'])
    #print (data['subject'])

    #arr = np.asarray(data)
    #print(arr)

def convert(db='b'):
    directory = "/home/istvan/capgMyo/unzipped-db"+db
    directory_out = "/home/istvan/capgMyo/data/db"+db
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".mat"):
                aFile = os.path.join(root, file)
                matFile = scipy.io.loadmat(aFile)
                np.save(os.path.join(directory_out, '{:03d}-{:03d}-{:03d}.npy'.format(matFile['subject'][0][0], matFile['gesture'][0][0], matFile['trial'][0][0])), matFile['data'])

def dot_product(x, kernel):
    """
    Wrapper for dot product operation, in order to be compatible with both
    Theano and Tensorflow
    Args:
        x (): input
        kernel (): weights
    Returns:
    """
    if K.backend() == 'tensorflow':
        # todo: check that this is correct
        return K.squeeze(K.dot(x, K.expand_dims(kernel)), axis=-1)
    else:
        return K.dot(x, kernel)


class Attention(Layer):
    def __init__(self,
                 W_regularizer=None, b_regularizer=None,
                 W_constraint=None, b_constraint=None,
                 bias=True,
                 return_attention=False,
                 **kwargs):
        """
        Keras Layer that implements an Attention mechanism for temporal data.
        Supports Masking.
        Follows the work of Raffel et al. [https://arxiv.org/abs/1512.08756]
        # Input shape
            3D tensor with shape: `(samples, steps, features)`.
        # Output shape
            2D tensor with shape: `(samples, features)`.
        :param kwargs:
        Just put it on top of an RNN Layer (GRU/LSTM/SimpleRNN) with return_sequences=True.
        The dimensions are inferred based on the output shape of the RNN.
        Note: The layer has been tested with Keras 1.x
        Example:
        
            # 1
            model.add(LSTM(64, return_sequences=True))
            model.add(Attention())
            # next add a Dense layer (for classification/regression) or whatever...
            # 2 - Get the attention scores
            hidden = LSTM(64, return_sequences=True)(words)
            sentence, word_scores = Attention(return_attention=True)(hidden)
        """
        self.supports_masking = True
        self.return_attention = return_attention
        self.init = initializers.get('glorot_uniform')

        self.W_regularizer = regularizers.get(W_regularizer)
        self.b_regularizer = regularizers.get(b_regularizer)

        self.W_constraint = constraints.get(W_constraint)
        self.b_constraint = constraints.get(b_constraint)

        self.bias = bias
        super(Attention, self).__init__(**kwargs)

    def build(self, input_shape):
        assert len(input_shape) == 3

        self.W = self.add_weight(shape=(int(input_shape[-1]),), #, self.output_dim)
                                 initializer=self.init,
                                 name='{}_W'.format(self.name),
                                 regularizer=self.W_regularizer,
                                 constraint=self.W_constraint)
        if self.bias:
            self.b = self.add_weight(shape=(int(input_shape[1]),),
                                     initializer='zero',
                                     name='{}_b'.format(self.name),
                                     regularizer=self.b_regularizer,
                                     constraint=self.b_constraint)
        else:
            self.b = None

        self.built = True

    def compute_mask(self, input, input_mask=None):
        # do not pass the mask to the next layers
        return None

    def call(self, x, mask=None):
        eij = dot_product(x, self.W)

        if self.bias:
            eij += self.b

        eij = K.tanh(eij)

        a = K.exp(eij)

        # apply mask after the exp. will be re-normalized next
        if mask is not None:
            # Cast the mask to floatX to avoid float64 upcasting in theano
            a *= K.cast(mask, K.floatx())

        # in some cases especially in the early stages of training the sum may be almost zero
        # and this results in NaN's. A workaround is to add a very small positive number ε to the sum.
        # a /= K.cast(K.sum(a, axis=1, keepdims=True), K.floatx())
        a /= K.cast(K.sum(a, axis=1, keepdims=True) + K.epsilon(), K.floatx())

        weighted_input = x * K.expand_dims(a)

        result = K.sum(weighted_input, axis=1)

        if self.return_attention:
            return [result, a]
        return result

    def compute_output_shape(self, input_shape):
        if self.return_attention:
            return [(input_shape[0], input_shape[-1]),
                    (input_shape[0], input_shape[1])]
        else:
            return input_shape[0], input_shape[-1]

# Creating files
def sequenceBatchCreator(batchSize=1024, seq_len=150, strideMode=3, directory_out="numpy_seqs_stride3"):
    directory = "numpy"

    for i in range (11, 21): # second session
        for j in range (1, 9): # 8 gestures
            X = []
            y = []
            for k in range (1, 11): # 10 repetitions
                fileName = '{:03d}-{:03d}-{:03d}.npy'.format(i, j, k)
                aFile = np.load(os.path.join(directory, fileName))
                if strideMode == 1:
                    stride = 1
                elif strideMode == 2:                
                    stride = seq_len//2
                elif strideMode == 3:
                    stride = 50
                
                #numOfSequences = (aFile.shape[0]-seq_len)//stride + 1

                for l in range(0, aFile.shape[0]-seq_len+1, stride):
                    X.append(aFile[l:l+seq_len, :])
                    y.append(j-1) # cause of to_categorical()
            npX = np.array(X)
            np.save(os.path.join(directory_out,
                'X_{:03d}-{:03d}_{:03d}ms_sequences_{:03d}stride.npy'.format(i, j, seq_len, stride)),
                npX)
            npy = np.array(y)
            np.save(os.path.join(directory_out,
                'y_{:03d}-{:03d}_{:03d}ms_sequences_{:03d}stride.npy'.format(i, j, seq_len, stride)),
                npy)

def sequenceFixTrainingGenerator(batchSize=1024, seq_len=150, indexForValidation=20, strideMode=1):
    directory = "numpy"

    for i in sample(range(11, 21), 10): # second session
        if i == indexForValidation: continue
        for j in sample(range(1, 9), 8): # 8 gestures
            X_train = []
            y_train = []
            for k in sample(range(1, 11), 10): # 10 repetitions
                fileName = '{:03d}-{:03d}-{:03d}.npy'.format(i, j, k)
                aFile = np.load(os.path.join(directory, fileName))
                if strideMode == 1:
                    stride = 1
                elif strideMode == 2:                
                    stride = seq_len//2
                elif strideMode == 3:
                    stride = 50
                for l in range(0, aFile.shape[0]-seq_len+1, stride):
                    X_train.append(aFile[l:l+seq_len, :])
                    y_train.append(j-1) # cause of to_categorical()
            npX_train = np.array(X_train)
            yield npX_train, to_categorical(y_train, num_classes=number_of_classes)

def unisonShuffle(X, y):
  s = np.arange(X.shape[0])
  np.random.shuffle(s)
  return X[s], y[s]

def sequenceBatchGeneratorAbsMean2(batchSize,
    seq_len,
    indexes,
    stride,
    allNumOfSamples,
    meanOf,
    shuffling,
    standardize,
    amountOfRepetitions,
    amountOfGestures,
    totalGestures,
    totalRepetitions,
    directory,
    dataset,
    repetitions,
    number_of_classes):
    
    X = []
    y = []
    
    # DBC:
    # mean:	0.00011763552702395671
    # std:	0.008829399359531554
    if dataset == 'dbc':
        mean = 0.00011763552702395671
        std = 0.008829399359531554
    
    if shuffling:
        range_i = sample(indexes, len(indexes))
        range_j = sample(range(1, totalGestures+1), amountOfGestures)
        range_k = sample(range(1, totalRepetitions+1), amountOfRepetitions)
    else:
        range_i = indexes
        range_j = range(1, amountOfGestures+1)
        range_k = range(1, amountOfRepetitions+1)
    
    if repetitions is not None: # intra-session
        if len(repetitions) == 5: # intra-session
            if shuffling:
                range_k = sample(repetitions, amountOfRepetitions)
            else:
                range_k = repetitions[:amountOfRepetitions]
        
    counter = 0
    while True:
        for i in range_i: # users
            for j in range_j: # 8 gestures
                for k in range_k: # 10 repetitions
                    fileName = '{:03d}-{:03d}-{:03d}.npy'.format(i, j, k)
                    aFile = np.load(os.path.join(directory, fileName))
                    if standardize:
                        aFile = (aFile-mean)/std
                    aFile = np.abs(aFile)
                    absMeanFile = np.apply_along_axis(lambda m: np.convolve(m, np.ones((meanOf,))/meanOf, mode='valid'), axis=0, arr=aFile)
                    del aFile
                    for l in range(0, absMeanFile.shape[0]-seq_len+1, stride):
                        X.append(absMeanFile[l:l+seq_len, :])
                        y.append(j-1)
                        counter += 1
                        if counter % allNumOfSamples == 0:
                          #print('\ntraining counter: '+str(counter))
                          if shuffling:
                            yield unisonShuffle(np.array(X), to_categorical(y, num_classes=number_of_classes))
                          else:
                            yield np.array(X), to_categorical(y, num_classes=number_of_classes)
                          del X, y
                          X = []
                          y = []
                          counter = 0
                        elif counter % batchSize == 0:
                          #print('\ntraining counter: '+str(counter))
                          if shuffling:
                            yield unisonShuffle(np.array(X), to_categorical(y, num_classes=number_of_classes))
                          else:
                            yield np.array(X), to_categorical(y, num_classes=number_of_classes)
                          del X, y
                          X = []
                          y = []
                    del absMeanFile

def buildModel(classes, features, cellNeurons, cellDropout, denseDropout, denseNeurons, sequenceLength, stacked=False, bidirectional=False, l2=0.0):
    model = Sequential()
    model.add(TimeDistributed(Dense(features,
      kernel_initializer='identity',
      bias_initializer='zeros',
      name='customNn',
      activation=None), input_shape=(sequenceLength, features), name='td', trainable=False))
    if bidirectional:
        if stacked:
            model.add(Bidirectional(LSTM(cellNeurons, recurrent_dropout=cellDropout, name='rnn', trainable=True, return_sequences=True, kernel_regularizer=regularizers.l2(l2)), merge_mode='concat'))
            model.add(Bidirectional(LSTM(cellNeurons, recurrent_dropout=cellDropout, name='rnn_2nd_layer', trainable=True, kernel_regularizer=regularizers.l2(l2)), merge_mode='concat'))
        else:
            model.add(Bidirectional(LSTM(cellNeurons, recurrent_dropout=cellDropout, name='rnn', trainable=True, kernel_regularizer=regularizers.l2(l2)), merge_mode='concat'))
    else:
        if stacked:
            model.add(LSTM(cellNeurons, recurrent_dropout=cellDropout, name='rnn', trainable=True, return_sequences=True, kernel_regularizer=regularizers.l2(l2)))
            model.add(LSTM(cellNeurons, recurrent_dropout=cellDropout, name='rnn_2nd_layer', trainable=True, kernel_regularizer=regularizers.l2(l2)))
            #model.add(Attention(name='attention', trainable=True))
        else:
            model.add(LSTM(cellNeurons, recurrent_dropout=cellDropout, name='rnn', trainable=True, kernel_regularizer=regularizers.l2(l2)))
    model.add(Dense(denseNeurons, name='nn', trainable=True, kernel_regularizer=regularizers.l2(l2)))
    model.add(Dropout(denseDropout, name='nn_dropout', trainable=True))
    model.add(Dense(classes, activation="softmax", name='output_softmax', trainable=True, kernel_regularizer=regularizers.l2(l2)))
    #model.summary()
    
    if onTpu:
        model.compile(loss="categorical_crossentropy",
                optimizer=tf.train.AdamOptimizer(learning_rate=0.001),
                metrics=["accuracy"])
    
        multi_model = toTpuModel(model)
    else:
        multi_model = toMultiGpuModel(model)
        multi_model.compile(loss="categorical_crossentropy",
                optimizer=optimizers.Adam(lr=0.001, decay=0.0001),
                metrics=["accuracy"])

    return model, multi_model

class AltModelCheckpoint(ModelCheckpoint):
    def __init__(self, filepath, alternate_model, **kwargs):
        """
        Additional keyword args are passed to ModelCheckpoint; see those docs for information on what args are accepted.
        :param filepath:
        :param alternate_model: Keras model to save instead of the default. This is used especially when training multi-
                                gpu models built with Keras multi_gpu_model(). In that case, you would pass the original
                                "template model" to be saved each checkpoint.
        :param kwargs:          Passed to ModelCheckpoint.
        """

        self.alternate_model = alternate_model
        super().__init__(filepath, **kwargs)

    def on_epoch_end(self, epoch, logs=None):
        model_before = self.model
        self.model = self.alternate_model
        super().on_epoch_end(epoch, logs)
        self.model = model_before

def getAdaptationModel(modelPath, adaptationVersion, features, seqLen):
    fineTuneModel = load_model(modelPath)
    
    # Test optimizer's state:
    #print(fineTuneModel.optimizer.get_config())
    #print(dir(fineTuneModel.optimizer))
    #print(fineTuneModel.optimizer.lr)
    
    fineTuneModel.get_layer('td').trainable = True
    if adaptationVersion == 2:
        fineTuneModel.get_layer('td').activation = 'relu'
    fineTuneModel.get_layer('rnn').trainable = False
    if fineTuneModel.get_layer('rnn_2nd_layer') != None:
        fineTuneModel.get_layer('rnn_2nd_layer').trainable = False
    fineTuneModel.get_layer('nn').trainable = False
    fineTuneModel.get_layer('nn_dropout').trainable = False
    fineTuneModel.get_layer('output_softmax').trainable = False
    
    if adaptationVersion == 3:
        fineTuneModel.get_layer('td').activation = 'relu'
        fineTuneModel.name = "existingModel"
        newModel = Sequential()
        newModel.add(TimeDistributed(Dense(features,
            kernel_initializer='identity',
            bias_initializer='zeros',
            activation='relu'), input_shape=(seqLen, features), name='td0', trainable=True))
        newModel.add(fineTuneModel)
        fineTuneModel = newModel
    if adaptationVersion == 4: # initializer does not work with this initializer cause it is not square
        fineTuneModel.get_layer('td').activation = 'relu'
        fineTuneModel.name = "existingModel"
        newModel = Sequential()
        newModel.add(TimeDistributed(Dense(10*features,
            kernel_initializer='identity',
            bias_initializer='zeros',
            activation='relu'), input_shape=(seqLen, features), name='td0', trainable=True))
        newModel.add(fineTuneModel)
        fineTuneModel = newModel
    
    if onTpu:
        multiFineTuneModel.compile(loss="categorical_crossentropy",
                    optimizer=tf.train.AdamOptimizer(lr=0.001),
                    metrics=["accuracy"])
        multiFineTuneModel = toTpuModel(fineTuneModel)
    else:
        multiFineTuneModel = toMultiGpuModel(fineTuneModel)
        multiFineTuneModel.compile(loss="categorical_crossentropy",
                    optimizer=optimizers.Adam(lr=0.001),
                    metrics=["accuracy"])

    # Test optimizer's state:
    #print(fineTuneModel.optimizer.get_config())
    #print(dir(fineTuneModel.optimizer))
    #print(fineTuneModel.optimizer.lr)
    
    return fineTuneModel, multiFineTuneModel

def validationAccuracyValues(x):
    return(x[-10:-5])

def getBestModel(testUser, workingDirectory):
    file_list = os.listdir(workingDirectory+str(testUser))
    best = max(file_list, key=validationAccuracyValues)
    print('\nBest pre-trained model to start with: ' + str(best))
    return workingDirectory+str(testUser) + '/' + best

class WeightsNorm(Callback):
    def on_batch_end(self, batch, logs={}):
        # Norm clipping:
        print(str(math.sqrt(sum(np.sum(K.get_value(w)) for w in self.model.optimizer.weights))) + '\n')
        return

def checkdataStatistics():
    directory = "/home/istvan/capgMyo/data/dbc"
    
    bigArray = None
    
    isFirst = True
    for i in range (1, 11):
        for j in range (1, 9): # 8 gestures
            for k in range (1, 11): # 10 repetitions
                fileName = '{:03d}-{:03d}-{:03d}.npy'.format(i, j, k)
                aFile = np.load(os.path.join(directory, fileName))
                if isFirst:
                    bigArray = aFile
                    isFirst = False
                else:
                    bigArray = np.concatenate((bigArray, aFile), axis=0)
    mean, std = np.mean(bigArray), np.std(bigArray)
    print(mean)
    print(std)
    print(np.max(bigArray))
    print(np.min(bigArray))
    newArray = (bigArray - mean)/std
    mean2, std2 = np.mean(newArray), np.std(newArray)
    print(mean2)
    print(std2)
    print(np.max(newArray))
    print(np.min(newArray))
    plt.hist(bigArray, bins=10)
    plt.title("Histogram")
    plt.savefig('histogram-all.png')
    plt.close()
    plt.hist(newArray, bins=10)
    plt.title("Histogram")
    plt.savefig('histogram-new-all.png')

def preTrainingModel(trainingUsers,
        testUsers,
        allNumOfTrainingSamples,
        trainingStepsPerEpoch,
        allNumOfValidationSamples,
        validationStepsPerEpoch,
        amountOfRepetitions,
        amountOfGestures,
        preTrainingNumOfEpochs,
        trial,
        batchSize,
        totalGestures,
        totalRepetitions,
        directory,
        testUser,
        workingDirectory,
		trainingDataset,
		testDataset,
		trainingRepetitions,
		testRepetitions,
        number_of_classes,
        saveCheckpoints):
    
    base_model, multi_model = buildModel(classes=number_of_classes,
            features=NUMBER_OF_FEATURES,
            cellNeurons=cellNeurons,
            cellDropout=recurrent_dropout,
            denseDropout=dropout,
            denseNeurons=denseNeurons,
            sequenceLength=seq_len,
            stacked=True,
            bidirectional=False,
            l2=0.0)
    
    histories = {}
    
    path = workingDirectory+str(testUser)
    if not os.path.exists(path):
        os.makedirs(path)

    my_training_batch_generator = sequenceBatchGeneratorAbsMean2(batchSize=batchSize,
                                                            seq_len=seq_len,
                                                            indexes=trainingUsers,
                                                            stride=stride,
                                                            allNumOfSamples=allNumOfTrainingSamples,
                                                            meanOf=mean,
                                                            shuffling=True,
                                                            standardize=True,
                                                            amountOfRepetitions=totalRepetitions,
                                                            amountOfGestures=totalGestures,
                                                            totalGestures=totalGestures,
                                                            totalRepetitions=totalRepetitions,
                                                            directory=directory,
                                                            dataset=trainingDataset,
															repetitions=trainingRepetitions,
                                                            number_of_classes=number_of_classes)
    my_validation_batch_generator = sequenceBatchGeneratorAbsMean2(batchSize=batchSize,
                                                            seq_len=seq_len,
                                                            indexes=testUsers,
                                                            stride=stride,
                                                            allNumOfSamples=allNumOfValidationSamples,
                                                            meanOf=mean,
                                                            shuffling=False,
                                                            standardize=True,
                                                            amountOfRepetitions=totalRepetitions,
                                                            amountOfGestures=totalGestures,
                                                            totalGestures=totalGestures,
                                                            totalRepetitions=totalRepetitions,
                                                            directory=directory,
                                                            dataset=testDataset,
															repetitions=testRepetitions,
                                                            number_of_classes=number_of_classes)
    
    filepath=path + "/e{epoch:03d}-a{val_acc:.3f}.hdf5"
    
    #lrate = LearningRateScheduler(step_decay)
    if saveCheckpoints == True:
        if onTpu:
            modelCheckpoint = ModelCheckpoint(filepath, monitor='val_acc', verbose=0, save_best_only=True, mode='max')
        else:
            modelCheckpoint = AltModelCheckpoint(filepath, alternate_model=base_model, monitor='val_acc', verbose=0, save_best_only=True, mode='max')
        callbacks_list = [modelCheckpoint]
    else:
        callbacks_list = []
    
    startTime = int(round(time.time()))
    print("\n##### Start Time with test user "+str(testUser)+": "+str(startTime))
    histories[testUser] = multi_model.fit_generator(generator=my_training_batch_generator,
                                        steps_per_epoch=trainingStepsPerEpoch,
                                        epochs=preTrainingNumOfEpochs,
                                        #max_queue_size=5,
                                        verbose=2,
                                        callbacks=callbacks_list,
                                        validation_data=my_validation_batch_generator,
                                        validation_steps=validationStepsPerEpoch,
                                        use_multiprocessing=False)

    endTime = int(round(time.time()))
    print("\n##### End Time with test user "+str(testUser)+": "+str(endTime))
    toLog = str(preTrainingNumOfEpochs) + ',' + str(seq_len) + ',' + str(stride) + ',' + str(batchSize) + ',' + str(mean)
    with open(workingDirectory+"history.csv", "a") as myfile:
        myfile.write(str(endTime)\
            + ',' + str(trial)\
            + ',' + str(testUser)\
            + ',' + str(max(histories[testUser].history['acc']))\
            + ',' + str(max(histories[testUser].history['val_acc']))\
            + ',' + str(endTime-startTime)\
            + ',' + toLog\
            + ',' + str(amountOfRepetitions)\
            + ',' + str(amountOfGestures) + '\n')
    
    del histories
    del base_model, multi_model
    del my_training_batch_generator, my_validation_batch_generator

def adaptModel(fineTuneUsers, testUsers, allNumOfFineTuningSamples, fineTuningStepsPerEpoch, amountOfRepetitions, amountOfGestures,
        allNumOfValidationSamples,
        validationStepsPerEpoch,
        numberOfFineTuningEpochs,
        trial,
        batchSize,
        totalGestures,
        totalRepetitions,
        directory,
        testUser,
        workingDirectory,
		trainingDataset,
		testDataset,
		trainingRepetitions,
		testRepetitions,
        number_of_classes):
    
    base_model, multi_model = getAdaptationModel(modelPath=getBestModel(testUser, workingDirectory), adaptationVersion=1, features=NUMBER_OF_FEATURES, seqLen=seq_len)
    
    histories = {}
    
    path = workingDirectory+str(testUser)+'-adapted'
    if not os.path.exists(path):
        os.makedirs(path)

    my_training_batch_generator = sequenceBatchGeneratorAbsMean2(batchSize=batchSize,
                                                            seq_len=seq_len,
                                                            indexes=fineTuneUsers,
                                                            stride=stride,
                                                            allNumOfSamples=allNumOfFineTuningSamples,
                                                            meanOf=mean,
                                                            shuffling=True,
                                                            standardize=True,
                                                            amountOfRepetitions=amountOfRepetitions,
                                                            amountOfGestures=amountOfGestures,
                                                            totalGestures=totalGestures,
                                                            totalRepetitions=totalRepetitions,
                                                            directory=directory,
															dataset=trainingDataset,
															repetitions=trainingRepetitions,
                                                            number_of_classes=number_of_classes)
    my_validation_batch_generator = sequenceBatchGeneratorAbsMean2(batchSize=batchSize,
                                                            seq_len=seq_len,
                                                            indexes=testUsers,
                                                            stride=stride,
                                                            allNumOfSamples=allNumOfValidationSamples,
                                                            meanOf=mean,
                                                            shuffling=False,
                                                            standardize=True,
                                                            amountOfRepetitions=totalRepetitions,
                                                            amountOfGestures=totalGestures,
                                                            totalGestures=totalGestures,
                                                            totalRepetitions=totalRepetitions,
                                                            directory=directory,
															dataset=testDataset,
															repetitions=testRepetitions,
                                                            number_of_classes=number_of_classes)

    filepath=path + "/e{epoch:03d}-a{val_acc:.3f}.hdf5"
    #modelCheckpoint = AltModelCheckpoint(filepath, alternate_model=base_model, monitor='val_acc', verbose=0, save_best_only=True, mode='max')
    #csv_logger = CSVLogger(workingDirectory+'log_u' + str(testUser) + '-adapted.csv', append=True, separator=',')
    #callbacks_list = [modelCheckpoint]
    startTime = int(round(time.time()))
    print("\n##### Start Time with test user "+str(testUser)+": "+str(startTime))
    histories[testUser] = multi_model.fit_generator(generator=my_training_batch_generator,
                                        steps_per_epoch=fineTuningStepsPerEpoch,
                                        epochs=numberOfFineTuningEpochs,
                                        #max_queue_size=5,
                                        verbose=2,
                                        #callbacks=callbacks_list,
                                        validation_data=my_validation_batch_generator,
                                        validation_steps=validationStepsPerEpoch,
                                        use_multiprocessing=False)

    endTime = int(round(time.time()))
    print("\n##### End Time with test user "+str(testUser)+": "+str(endTime))
    toLog = str(numberOfFineTuningEpochs) + ',' + str(seq_len) + ',' + str(stride) + ',' + str(batchSize) + ',' + str(mean)
    with open(workingDirectory+"history-adapted.csv", "a") as myfile:
        myfile.write(str(endTime)\
            + ',' + str(trial)\
            + ',' + str(testUser)\
            + ',' + str(max(histories[testUser].history['acc']))\
            + ',' + str(max(histories[testUser].history['val_acc']))\
            + ',' + str(endTime-startTime)\
            + ',' + toLog\
            + ',' + str(amountOfRepetitions)\
            + ',' + str(amountOfGestures) + '\n')
    
    del histories
    del base_model, multi_model
    del my_training_batch_generator, my_validation_batch_generator

#fineTuningEpochList = [1, 2, 4, 8, 16, 32, 64]
#fineTuningRepetitionList = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
#fineTuningGestureList = []
fineTuningEpochList = [100]
fineTuningRepetitionList = [10]
fineTuningGestureList = [12]

preTrainingNumOfEpochs = 200
seq_len = 150
stride = 70
mean=11
#seq_len = 40
#stride = 19
#mean=11
totalGestures=12
totalRepetitions=10

def generalScenario(validation, training):
    for trial in range(513, 514):
        for numberOfFineTuningEpochs in fineTuningEpochList:
            for amountOfGestures in fineTuningGestureList:
                for amountOfRepetitions in fineTuningRepetitionList:
                    ###################################################################################
                    # seq_len = 40
                    # stride = 19
                    # mean = 11
                    # -> allNumOfFineTuningSamples is a multiple of 51
                    # 1020 worked pretty well on GPU
                    ###################################################################################
                    # seq_len = 150
                    # stride = 70
                    # mean = 11
                    # -> allNumOfFineTuningSamples is a multiple of 13
                    # 12*5 * 13 (780) would work pretty well on TPU both for training and validation as well
                    ###################################################################################
                    #if amountOfGestures * amountOfRepetitions >= 80: # this is dynamic (20 for: sl 40, 51 for sl 150) haat....
                    batchSize = 780
                    #else:
                    #    batchSize = amountOfGestures * amountOfRepetitions * 13
                    
                    if validation == 'inter-subject':
                        allNumOfFineTuningSamples = 1 * amountOfGestures * amountOfRepetitions * ((1000-mean+1-seq_len)//stride + 1)
                        fineTuningStepsPerEpoch = allNumOfFineTuningSamples // batchSize
                        allNumOfValidationSamples = 1 * 12 * 10 * ((1000-mean+1-seq_len)//stride + 1)
                        validationStepsPerEpoch = allNumOfValidationSamples // batchSize
                        allNumOfTrainingSamples = 9 * 12 * 10 * ((1000-mean+1-seq_len)//stride + 1)
                        trainingStepsPerEpoch = allNumOfTrainingSamples // batchSize
                        directory = baseDrive+"data/dbc"
                        workingDirectory = baseDrive+'data/inter-subject/dbc/'
                        trainingUsers = list(range(1, 11))
                        for testUser in trainingUsers: # LOSOCV for inter-subject validation
                            fineTuneUsers = [testUser]
                            testUsers = [testUser]
                            currentTrainingUsers = trainingUsers.copy()
                            currentTrainingUsers.remove(testUser)
                            if training == 'pre-training':
                                preTrainingModel(trainingUsers=currentTrainingUsers,
                                    testUsers=testUsers,
                                    allNumOfTrainingSamples=allNumOfTrainingSamples,
                                    trainingStepsPerEpoch=trainingStepsPerEpoch,
                                    allNumOfValidationSamples=allNumOfValidationSamples,
                                    validationStepsPerEpoch=validationStepsPerEpoch,
                                    amountOfRepetitions=amountOfRepetitions,
                                    amountOfGestures=amountOfGestures,
                                    preTrainingNumOfEpochs=preTrainingNumOfEpochs,
                                    trial=trial,
                                    batchSize=batchSize,
                                    totalGestures=totalGestures,
                                    totalRepetitions=totalRepetitions,
                                    directory=directory,
                                    testUser=testUser,
                                    workingDirectory=workingDirectory,
									trainingDataset='dbc',
									testDataset='dbc',
									trainingRepetitions=None,
									testRepetitions=None,
                                    number_of_classes=number_of_classes,
                                    saveCheckpoints=True)
                            elif training == 'fine-tuning':
                                adaptModel(fineTuneUsers=fineTuneUsers,
                                    testUsers=testUsers,
                                    allNumOfFineTuningSamples=allNumOfFineTuningSamples,
                                    fineTuningStepsPerEpoch=fineTuningStepsPerEpoch,
                                    allNumOfValidationSamples=allNumOfValidationSamples,
                                    validationStepsPerEpoch=validationStepsPerEpoch,
                                    amountOfRepetitions=amountOfRepetitions,
                                    amountOfGestures=amountOfGestures,
                                    numberOfFineTuningEpochs=numberOfFineTuningEpochs,
                                    trial=trial,
                                    batchSize=batchSize,
                                    totalGestures=totalGestures,
                                    totalRepetitions=totalRepetitions,
                                    directory=directory,
                                    testUser=testUser,
                                    workingDirectory=workingDirectory,
									trainingDataset='dbc',
									testDataset='dbc',
									trainingRepetitions=None,
									testRepetitions=None,
                                    number_of_classes=number_of_classes)
                    if validation == 'inter-subject-split':
                        directory = baseDrive+"data/dbc"
                        workingDirectory = baseDrive+'data/inter-subject-split/dbc/'
                        trainingUsers = list(range(1, 11))
                        for testUser in trainingUsers: # LOSOCV for inter-subject validation
                            testUsers = [testUser]
                            if training == 'pre-training':
                                currentTrainingUsers = trainingUsers.copy()
                                currentTrainingUsers.remove(testUser)
                                allNumOfTrainingSamples = 9 * 12 * 10 * ((1000-mean+1-seq_len)//stride + 1)
                                trainingStepsPerEpoch = allNumOfTrainingSamples // batchSize
                                allNumOfValidationSamples = 1 * 12 * 10 * ((1000-mean+1-seq_len)//stride + 1)
                                validationStepsPerEpoch = allNumOfValidationSamples // batchSize
                                preTrainingModel(trainingUsers=currentTrainingUsers,
                                    testUsers=testUsers,
                                    allNumOfTrainingSamples=allNumOfTrainingSamples,
                                    trainingStepsPerEpoch=trainingStepsPerEpoch,
                                    allNumOfValidationSamples=allNumOfValidationSamples,
                                    validationStepsPerEpoch=validationStepsPerEpoch,
                                    amountOfRepetitions=10,
                                    amountOfGestures=12,
                                    preTrainingNumOfEpochs=preTrainingNumOfEpochs,
                                    trial=trial,
                                    batchSize=batchSize,
                                    totalGestures=12,
                                    totalRepetitions=10,
                                    directory=directory,
                                    testUser=testUser,
                                    workingDirectory=workingDirectory,
									trainingDataset='dbc',
									testDataset='dbc',
									trainingRepetitions=None,
									testRepetitions=None,
                                    number_of_classes=number_of_classes,
                                    saveCheckpoints=True)
                            elif training == 'fine-tuning':
                                trainingRepetitions = [1, 3, 5, 7, 9]
                                testRepetitions = [2, 4, 6, 8, 10]
                                fineTuneUsers = [testUser]
                                allNumOfFineTuningSamples = 1 * amountOfGestures * 5 * ((1000-mean+1-seq_len)//stride + 1)
                                fineTuningStepsPerEpoch = allNumOfFineTuningSamples // batchSize
                                allNumOfValidationSamples = 1 * 12 * 5 * ((1000-mean+1-seq_len)//stride + 1)
                                validationStepsPerEpoch = allNumOfValidationSamples // batchSize
                                adaptModel(fineTuneUsers=fineTuneUsers,
                                    testUsers=testUsers,
                                    allNumOfFineTuningSamples=allNumOfFineTuningSamples,
                                    fineTuningStepsPerEpoch=fineTuningStepsPerEpoch,
                                    allNumOfValidationSamples=allNumOfValidationSamples,
                                    validationStepsPerEpoch=validationStepsPerEpoch,
                                    amountOfRepetitions=5,
                                    amountOfGestures=amountOfGestures,
                                    numberOfFineTuningEpochs=numberOfFineTuningEpochs,
                                    trial=trial,
                                    batchSize=batchSize,
                                    totalGestures=totalGestures,
                                    totalRepetitions=5,
                                    directory=directory,
                                    testUser=testUser,
                                    workingDirectory=workingDirectory,
									trainingDataset='dbc',
									testDataset='dbc',
									trainingRepetitions=trainingRepetitions,
									testRepetitions=testRepetitions,
                                    number_of_classes=number_of_classes)
                    elif validation == 'intra-session':
                        allNumOfValidationSamples = 10 * 12 * 5 * ((1000-mean+1-seq_len)//stride + 1)
                        validationStepsPerEpoch = allNumOfValidationSamples // batchSize
                        allNumOfTrainingSamples = 10 * 12 * 5 * ((1000-mean+1-seq_len)//stride + 1)
                        trainingStepsPerEpoch = allNumOfTrainingSamples // batchSize
                        directory = baseDrive+"data/dbc"
                        workingDirectory = baseDrive+'data/intra-session/dbc/'
                        trainingUsers = list(range(1, 11))
                        testUsers = list(range(1, 11))
                        trainingRepetitions = [1, 3, 5, 7, 9]
                        testRepetitions = [2, 4, 6, 8, 10]
                        testUser='evenRepetitions'
                        if training == 'pre-training':
                            preTrainingModel(trainingUsers=trainingUsers,
                                testUsers=testUsers,
                                allNumOfTrainingSamples=allNumOfTrainingSamples,
                                trainingStepsPerEpoch=trainingStepsPerEpoch,
                                allNumOfValidationSamples=allNumOfValidationSamples,
                                validationStepsPerEpoch=validationStepsPerEpoch,
                                amountOfRepetitions=5,
                                amountOfGestures=amountOfGestures,
                                preTrainingNumOfEpochs=preTrainingNumOfEpochs,
                                trial=trial,
                                batchSize=batchSize,
                                totalGestures=totalGestures,
                                totalRepetitions=5,
                                directory=directory,
                                testUser=testUser,
                                workingDirectory=workingDirectory,
                                trainingDataset='dbc',
                                testDataset='dbc',
                                trainingRepetitions=trainingRepetitions,
                                testRepetitions=testRepetitions,
                                number_of_classes=number_of_classes,
                                saveCheckpoints=False)
                    elif validation == 'intra-session-separated':
                        allNumOfValidationSamples = 1 * 12 * 5 * ((1000-mean+1-seq_len)//stride + 1)
                        validationStepsPerEpoch = allNumOfValidationSamples // batchSize
                        allNumOfTrainingSamples = 1 * 12 * 5 * ((1000-mean+1-seq_len)//stride + 1)
                        trainingStepsPerEpoch = allNumOfTrainingSamples // batchSize
                        directory = baseDrive+"data/dbc"
                        workingDirectory = baseDrive+'data/intra-session-separated/dbc/'
                        trainingUsers = list(range(1, 11))
                        for subject in trainingUsers:
                            print("subject: " + str(subject))
                            trainingUsers = [subject]
                            testUsers = [subject]
                            trainingRepetitions = [1, 3, 5, 7, 9]
                            testRepetitions = [2, 4, 6, 8, 10]
                            testUser=subject
                            if training == 'pre-training':
                                preTrainingModel(trainingUsers=trainingUsers,
                                    testUsers=testUsers,
                                    allNumOfTrainingSamples=allNumOfTrainingSamples,
                                    trainingStepsPerEpoch=trainingStepsPerEpoch,
                                    allNumOfValidationSamples=allNumOfValidationSamples,
                                    validationStepsPerEpoch=validationStepsPerEpoch,
                                    amountOfRepetitions=5,
                                    amountOfGestures=amountOfGestures,
                                    preTrainingNumOfEpochs=preTrainingNumOfEpochs,
                                    trial=trial,
                                    batchSize=batchSize,
                                    totalGestures=totalGestures,
                                    totalRepetitions=5,
                                    directory=directory,
                                    testUser=testUser,
                                    workingDirectory=workingDirectory,
                                    trainingDataset='dbc',
                                    testDataset='dbc',
                                    trainingRepetitions=trainingRepetitions,
                                    testRepetitions=testRepetitions,
                                    number_of_classes=number_of_classes,
                                    saveCheckpoints=False)

#generalScenario(validation='intra-session-separated', training='pre-training')
generalScenario(validation='inter-subject-split', training='pre-training')
generalScenario(validation='inter-subject-split', training='fine-tuning')