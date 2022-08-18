#
# Copyright (c) 2021-2022, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import os
import sys
import cv2
import numpy as np
import onnx
import onnx_graphsurgeon as gs

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
import tensorflow as tf
from datetime import datetime as dt
from cuda import cudart
import tensorrt as trt

dataPath = os.path.dirname(os.path.realpath(__file__)) + "/../../00-MNISTData/"
sys.path.append(dataPath)
import loadMnistData

tf.compat.v1.disable_eager_execution()
np.random.seed(97)
tf.compat.v1.set_random_seed(97)
nTrainbatchSize = 128
nInferBatchSize = 1
pbFile0 = "./model0.pb"
pbFile1 = "./model1.pb"
onnxFile0 = "./model0.onnx"
onnxFile1 = "./model1.onnx"
trtFile = "./model.plan"
inferenceImage = dataPath + "8.png"

os.system("rm -rf ./*.pb ./*.onnx ./*.plan")
np.set_printoptions(precision=4, linewidth=200, suppress=True)
cudart.cudaDeviceSynchronize()

# TensorFlow 中创建网络并保存为 .pb 文件 -------------------------------------------
x = tf.compat.v1.placeholder(tf.float32, [None, 28, 28, 1], name="x")
y_ = tf.compat.v1.placeholder(tf.float32, [None, 10], name="y_")

w1 = tf.compat.v1.get_variable("w1", shape=[5, 5, 1, 32], initializer=tf.truncated_normal_initializer(mean=0, stddev=0.1))
b1 = tf.compat.v1.get_variable("b1", shape=[32], initializer=tf.constant_initializer(value=0.1))
h1 = tf.nn.conv2d(x, w1, strides=[1, 1, 1, 1], padding="SAME")
h2 = h1 + b1
h3 = tf.nn.relu(h2)
h4 = tf.nn.max_pool2d(h3, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding="SAME")

w2 = tf.compat.v1.get_variable("w2", shape=[5, 5, 32, 64], initializer=tf.truncated_normal_initializer(mean=0, stddev=0.1))
b2 = tf.compat.v1.get_variable("b2", shape=[64], initializer=tf.constant_initializer(value=0.1))
h5 = tf.nn.conv2d(h4, w2, strides=[1, 1, 1, 1], padding="SAME")
h6 = h5 + b2
h7 = tf.nn.relu(h6)
h8 = tf.nn.max_pool2d(h7, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding="SAME")

w3 = tf.compat.v1.get_variable("w3", shape=[7 * 7 * 64, 1024], initializer=tf.truncated_normal_initializer(mean=0, stddev=0.1))
b3 = tf.compat.v1.get_variable("b3", shape=[1024], initializer=tf.constant_initializer(value=0.1))
h9 = tf.reshape(h8, [-1, 7 * 7 * 64])
h10 = tf.matmul(h9, w3)
h11 = h10 + b3
h12 = tf.nn.relu(h11)

w4 = tf.compat.v1.get_variable("w4", shape=[1024, 10], initializer=tf.truncated_normal_initializer(mean=0, stddev=0.1))
b4 = tf.compat.v1.get_variable("b4", shape=[10], initializer=tf.constant_initializer(value=0.1))
h13 = tf.matmul(h12, w4)
h14 = h13 + b4
y = tf.nn.softmax(h14, name="y")
z = tf.argmax(y, 1, name="z")

crossEntropy = -tf.reduce_sum(y_ * tf.math.log(y))
trainStep = tf.compat.v1.train.AdamOptimizer(1e-4).minimize(crossEntropy)

output = tf.argmax(y, 1)
resultCheck = tf.equal(tf.argmax(y, 1), tf.argmax(y_, 1))
acc = tf.reduce_mean(tf.cast(resultCheck, tf.float32), name="acc")

tfConfig = tf.compat.v1.ConfigProto()
tfConfig.gpu_options.per_process_gpu_memory_fraction = 0.5
sess = tf.compat.v1.Session(config=tfConfig)
sess.run(tf.compat.v1.global_variables_initializer())

constantGraph = tf.graph_util.convert_variables_to_constants(sess, sess.graph_def, ["z"])
with tf.gfile.FastGFile(pbFile0, mode="wb") as f:
    f.write(constantGraph.SerializeToString())

mnist = loadMnistData.MnistData(dataPath, isOneHot=True)
for i in range(1000):
    xSample, ySample = mnist.getBatch(nTrainbatchSize, True)
    trainStep.run(session=sess, feed_dict={x: xSample, y_: ySample})
    if i % 100 == 0:
        train_acc = acc.eval(session=sess, feed_dict={x: xSample, y_: ySample})
        print("%s, step %d, acc = %f" % (dt.now(), i, train_acc))

xSample, ySample = mnist.getBatch(100, False)
print("%s, test acc = %f" % (dt.now(), acc.eval(session=sess, feed_dict={x: xSample, y_: ySample})))

constantGraph = tf.graph_util.convert_variables_to_constants(sess, sess.graph_def, ["z"])
with tf.gfile.FastGFile(pbFile1, mode="wb") as f:
    f.write(constantGraph.SerializeToString())
sess.close()
print("Succeeded building model in TensorFlow!")

# 将 .pb 文件转换为 .onnx 文件 --------------------------------------------------
os.system("python3 -m tf2onnx.convert --input %s --output %s --inputs 'x:0' --outputs 'z:0' --inputs-as-nchw 'x:0'" % (pbFile0, onnxFile0))
os.system("python3 -m tf2onnx.convert --input %s --output %s --inputs 'x:0' --outputs 'z:0' --inputs-as-nchw 'x:0'" % (pbFile1, onnxFile1))
print("Succeeded converting model into onnx!")

# refit + dynamic shape since TensorRT8.5，这里先把它改成 static shape
for file in [onnxFile0, onnxFile1]:
    graph = gs.import_onnx(onnx.load(file))
    graph.inputs[0].shape = [nInferBatchSize, 1, 28, 28]
    graph.cleanup()
    onnx.save(gs.export_onnx(graph), file)
print("Succeeded converting model into static shape!")

# TensorRT 中加载 .onnx 创建 engine --------------------------------------------
def run():
    logger = trt.Logger(trt.Logger.WARNING)
    if os.path.isfile(trtFile):
        with open(trtFile, "rb") as f:
            engine = trt.Runtime(logger).deserialize_cuda_engine(f.read())
        if engine == None:
            print("Failed loading engine!")
            exit()
        print("Succeeded loading engine!")
        onnxFile = onnxFile1  # 已经有 model.plan，加载 model1.onnx 来做 Refit
    else:
        onnxFile = onnxFile0  # 还没有 model.plan，加载 model0.onnx 构建 model.plan

    builder = trt.Builder(logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    config = builder.create_builder_config()
    config.flags = 1 << int(trt.BuilderFlag.REFIT)
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 3 << 30)
    parser = trt.OnnxParser(network, logger)
    if not os.path.exists(onnxFile):
        print("Failed finding .onnx file!")
        exit()
    print("Succeeded finding .onnx file!")
    with open(onnxFile, "rb") as model:
        if not parser.parse(model.read()):
            print("Failed parsing .onnx file!")
            for error in range(parser.num_errors):
                print(parser.get_error(error))
            exit()
        print("Succeeded parsing .onnx file!")

    if os.path.isfile(trtFile):  # 进行 Refit
        refitter = trt.Refitter(engine, logger)
        layerNameList, weightRoleList = refitter.get_all()
        print("[Name,\tRole]")
        for name, role in zip(layerNameList, weightRoleList):
            print("[%s,%s]" % (name, role))

        for i in range(network.num_layers):
            layer = network.get_layer(i)
            if layer.name in layerNameList:

                if layer.type == trt.LayerType.CONVOLUTION:
                    layer.__class__ = trt.IConvolutionLayer
                    refitter.set_weights(layer.name, trt.WeightsRole.KERNEL, layer.kernel)
                    refitter.set_weights(layer.name, trt.WeightsRole.BIAS, layer.bias)

                if layer.type == trt.LayerType.FULLY_CONNECTED:
                    layer.__class__ = trt.IFullyConnectedLayer
                    refitter.set_weights(layer.name, trt.WeightsRole.KERNEL, layer.kernel)

                if layer.type == trt.LayerType.CONSTANT:
                    layer.__class__ = trt.IConstantLayer
                    refitter.set_weights(layer.name, trt.WeightsRole.CONSTANT, layer.weights)

                if True:  # 据实际网络情况，可能需要添加更多判断
                    pass

        refitter.refit_cuda_engine()

    else:  # 构建 model.plan
        """
        # 逐层打印网络信息
        for i in range(network.num_layers):
            layer = network.get_layer(i)        
            print(i, "%s,in=%d,out=%d,%s" % (str(layer.type)[10:], layer.num_inputs, layer.num_outputs, layer.name))
            for j in range(layer.num_inputs):
                tensor = layer.get_input(j)
                if tensor == None:
                    print("\tInput  %2d:" % j, "None")
                else:
                    print("\tInput  %2d:%s,%s,%s" % (j, tensor.shape, str(tensor.dtype)[9:], tensor.name))
            for j in range(layer.num_outputs):
                tensor = layer.get_output(j)
                if tensor == None:
                    print("\tOutput %2d:" % j, "None")
                else:
                    print("\tOutput %2d:%s,%s,%s" % (j, tensor.shape, str(tensor.dtype)[9:], tensor.name))
        """
        engineString = builder.build_serialized_network(network, config)
        if engineString == None:
            print("Failed building engine!")
            exit()
        print("Succeeded building engine!")
        with open(trtFile, "wb") as f:
            f.write(engineString)
        engine = trt.Runtime(logger).deserialize_cuda_engine(engineString)

    context = engine.create_execution_context()
    #print("Binding all? %s"%(["No","Yes"][int(context.all_binding_shapes_specified)]))
    nInput = np.sum([engine.binding_is_input(i) for i in range(engine.num_bindings)])
    nOutput = engine.num_bindings - nInput
    #for i in range(engine.num_bindings):
    #    print("Bind[%2d]:i[%d]->"%(i,i) if engine.binding_is_input(i) else "Bind[%2d]:o[%d]->"%(i,i-nInput),
    #            engine.get_binding_dtype(i),engine.get_binding_shape(i),context.get_binding_shape(i),engine.get_binding_name(i))

    data = cv2.imread(inferenceImage, cv2.IMREAD_GRAYSCALE).astype(np.float32)
    data = np.tile(data, [nInferBatchSize, 1, 1, 1])
    bufferH = []
    bufferH.append(data)
    for i in range(nOutput):
        bufferH.append(np.empty(context.get_binding_shape(nInput + i), dtype=trt.nptype(engine.get_binding_dtype(nInput + i))))
    bufferD = []
    for i in range(engine.num_bindings):
        bufferD.append(cudart.cudaMalloc(bufferH[i].nbytes)[1])

    for i in range(nInput):
        cudart.cudaMemcpy(bufferD[i], np.ascontiguousarray(bufferH[i].reshape(-1)).ctypes.data, bufferH[i].nbytes, cudart.cudaMemcpyKind.cudaMemcpyHostToDevice)

    context.execute_v2(bufferD)

    for i in range(nOutput):
        cudart.cudaMemcpy(bufferH[nInput + i].ctypes.data, bufferD[nInput + i], bufferH[nInput + i].nbytes, cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost)

    print("inputH0 :", bufferH[0].shape)
    print("outputH0:", bufferH[-1].shape)
    print(bufferH[-1])
    for buffer in bufferD:
        cudart.cudaFree(buffer)
    print("Succeeded running model in TensorRT!")

run()  # 构建 model.plan
run()  # 对 model.plan 做 Refit