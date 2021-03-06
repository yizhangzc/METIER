import argparse
import math
import os
import random
import sys
import time

import numpy as np
import tensorflow as tf
import tensorflow.contrib.slim as slim
from scipy.fftpack import fft
from sklearn import utils as skutils
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.neighbors import KNeighborsClassifier

import data_loader
import model


class my_model( object ):

    def __init__( self, version, gpu, fold, save_dir, dataset, framework):
        
        self._dataset   = dataset
        self._gpu       = gpu
        self._log_path  = dataset._path+'log/'+version+'/'
        self._fold      = fold % 10
        self._save_path = dataset._path+'record/'+save_dir
        self._framework = framework

        self._iter_steps        = 50000
        self._print_interval    = 100
        self._batch_size        = 100
        self._min_lr            = 0.0005
        self._max_lr            = 0.0015
        self._decay_speed       = 10000

        self._data_pos          = 0

    def load_data( self ):

        print( "loading the data..." )
        train_data, train_la, train_lu, test_data, test_la, test_lu = self._dataset.load_data( step=self._fold )

        self._train_data, self._train_la, self._train_lu = skutils.shuffle( train_data, train_la, train_lu )
        self._test_data     = test_data
        self._test_la       = test_la
        self._test_lu       = test_lu

        print( "finished data loading!" )
        print(  "train data shape: {}\n".format( self._train_data.shape ) +\
                "train la shape: {}\n".format( self._train_la.shape ) +\
                "train lu shape: {}\n".format( self._train_lu.shape ) +\
                "test data shape: {}\n".format( self._test_data.shape ) +\
                "test la shape: {}\n".format( self._test_la.shape ) +\
                "test lu shape: {}\n".format( self._test_lu.shape ) )

    def one_hot( self, y, n_values ):
        return np.eye( n_values )[ np.array( y, dtype = np.int32 ) ]

    def next_batch( self ):
        train_size  = self._train_data.shape[0]
        scale       = self._data_pos+self._batch_size
        if scale > train_size:
            a   = scale - train_size

            data1   = self._train_data[self._data_pos: ]
            la1     = self._train_la[self._data_pos: ]
            lu1     = self._train_lu[self._data_pos: ]
            
            # shuffle after one cycle
            self._train_data, self._train_la, self._train_lu = skutils.shuffle( self._train_data, self._train_la, self._train_lu )

            data2   = self._train_data[: a]
            la2     = self._train_la[: a]
            lu2     = self._train_lu[: a]

            data    = np.concatenate( (data1, data2), axis=0 )
            la      = np.concatenate( (la1, la2), axis=0 )
            lu      = np.concatenate( (lu1, lu2), axis=0 )
            
            self._data_pos = a
            return data, self.one_hot( la, self._dataset._train_act_num ), self.one_hot( lu, self._dataset._train_user_num )
        else:
            data    = self._train_data[self._data_pos: scale]
            la      = self._train_la[self._data_pos: scale]
            lu      = self._train_lu[self._data_pos: scale]

            self._data_pos = scale
            return data, self.one_hot( la, self._dataset._train_act_num ), self.one_hot( lu, self._dataset._train_user_num )

    def build_model( self ):
        self._is_training   = tf.placeholder( dtype = tf.bool )
        self._learning_rate = tf.placeholder( dtype = tf.float32 )

        self._X             = tf.placeholder( dtype = tf.float32,   shape = self._dataset._data_shape )
        self._YA            = tf.placeholder( dtype = tf.int32,     shape = [ None, self._dataset._train_act_num] )
        self._YU            = tf.placeholder( dtype = tf.int32,     shape = [ None, self._dataset._train_user_num] )

        if self._framework == 1:
            self._model = model.MTLMA_pretrain()
        elif self._framework == 2:
            self._model = model.MTLMA_train()
        else:
            print( 'model error!!!' )
            exit(0)

        a_preds, a_loss, u_preds, u_loss = self._model( self._X, self._YA, self._YU, self._dataset._train_act_num, self._dataset._train_user_num,
                                        self._dataset._winlen, self._dataset._name, self._fold, self._is_training)

        a_train_step    = tf.train.AdamOptimizer( self._learning_rate ).minimize( a_loss, var_list=self._model.get_act_step_vars() )
        u_train_step    = tf.train.AdamOptimizer( self._learning_rate ).minimize( u_loss, var_list=self._model.get_user_step_vars() )

        tf.summary.scalar( "learning rate", self._learning_rate )
        merged          = tf.summary.merge_all()
        update_ops      = tf.get_collection( tf.GraphKeys.UPDATE_OPS )

        self._a_preds       = a_preds
        self._u_preds       = u_preds
        self._a_train_step  = a_train_step
        self._u_train_step  = u_train_step
        self._merged        = merged
        self._update_ops    = update_ops
    

    def predict( self, sess ):

        size        = self._test_data.shape[0]
        batch_size  = self._batch_size

        LAPreds     = np.empty( [0] )
        LATruth     = np.empty( [0] )
        LUPreds     = np.empty( [0] )
        LUTruth     = np.empty( [0] )

        for start, end in zip(  range( 0,           size,               batch_size ),
                                range( batch_size,  size + batch_size,  batch_size ) ):
            end = end if end < size else size

            la_preds, lu_preds = sess.run( [self._a_preds, self._u_preds], feed_dict = {
                    self._X:            self._test_data[start: end],
                    self._is_training:  False
                } )

            LAPreds = np.concatenate( (LAPreds, np.argmax(la_preds, 1)) )
            LATruth = np.concatenate( (LATruth, self._test_la[start: end]) )

            LUPreds = np.concatenate( (LUPreds, np.argmax(lu_preds, 1)) )
            LUTruth = np.concatenate( (LUTruth, self._test_lu[start: end]) )

        return LATruth, LAPreds, LUTruth, LUPreds

    def save_paremeters( self, sess ):

        # import pdb; pdb.set_trace()
        for i in range( 1, 4, 1 ):
            TensorA = tf.get_collection( tf.GraphKeys.TRAINABLE_VARIABLES, scope='act_network/a_conv{}'.format(i) )
            TensorU = tf.get_collection( tf.GraphKeys.TRAINABLE_VARIABLES, scope='user_network/u_conv{}'.format(i) )
            ParameterA, ParameterU = sess.run( [TensorA, TensorU] )
            np.save( "./data/parameters/{}f{}a{}".format( self._dataset._name, self._fold, i), ParameterA[0] )
            np.save( "./data/parameters/{}f{}u{}".format( self._dataset._name, self._fold, i), ParameterU[0] )

    def run_model( self ):

        os.environ["CUDA_VISIBLE_DEVICES"] = str( self._gpu ) # gpu selection        
        sess_config = tf.ConfigProto()  
        sess_config.gpu_options.per_process_gpu_memory_fraction = 1  # 100% gpu
        sess_config.gpu_options.allow_growth = True      # dynamic growth

        with tf.Session( config = sess_config ) as sess:
            sess.run(tf.global_variables_initializer())
            sess.run(tf.local_variables_initializer())
            train_writer    = tf.summary.FileWriter( self._log_path + '/train', graph = tf.get_default_graph() )

            # result_array    = np.empty( [0, 2, len( self._test_data )] )
            LARecord = np.empty( [0, 2, self._test_data.shape[0]] )
            LURecord = np.empty( [0, 2, self._test_data.shape[0]] )

            for i in range( self._iter_steps ):

                data, la, lu    = self.next_batch()
                lr              = self._min_lr + ( self._max_lr - self._min_lr ) * math.exp( -i / self._decay_speed )

                if self._framework == 1:
                    summary, _, _, _ = sess.run( [self._merged, self._update_ops, self._a_train_step, self._u_train_step], feed_dict ={
                        self._X:                data,
                        self._YA:               la,
                        self._YU:               lu,
                        self._learning_rate:    lr,
                        self._is_training:      True } )
                elif self._framework == 2:
                    summary, _, _ = sess.run( [self._merged, self._update_ops, self._a_train_step], feed_dict ={
                        self._X:                data,
                        self._YA:               la,
                        self._YU:               lu,
                        self._learning_rate:    lr,
                        self._is_training:      True } )
                else:
                    print( "model error" )
                    exit()

                train_writer.add_summary( summary, i )

                if i % self._print_interval == 0:

                    LATruth, LAPreds, LUTruth, LUPreds = self.predict( sess )

                    LARecord    = np.append( LARecord, np.expand_dims( np.vstack((LATruth, LAPreds)), 0), axis=0 )
                    LURecord    = np.append( LURecord, np.expand_dims( np.vstack((LUTruth, LUPreds)), 0), axis=0 )

                    AAccuracy   = accuracy_score( LATruth, LAPreds, range( self._dataset._act_num ) )
                    Af1         = f1_score( LATruth, LAPreds, range( self._dataset._act_num ), average='macro' )

                    UAccuracy   = accuracy_score( LUTruth, LUPreds, range( self._dataset._user_num ) )
                    Uf1         = f1_score( LUTruth, LUPreds, range( self._dataset._user_num ), average='macro' )

                    print( "step: {},   AAccuracy: {},  Af1: {},  UAccuracy: {},  Uf1: {}".format( i, AAccuracy, Af1, UAccuracy, Uf1 ) )

                    if self._framework == 1 and i >= 10000:
                        self.save_paremeters( sess )
                        exit()

            result_path = self._save_path + "/"
            if not os.path.exists( result_path ):
                os.mkdir( result_path )
            
            LARecordFile    = result_path + "AR_fold{}_".format( self._fold ) + time.strftime( '%Y%m%d%H%M%S', time.localtime(time.time()))
            LURecordFile    = result_path + "UR_fold{}_".format( self._fold ) + time.strftime( '%Y%m%d%H%M%S', time.localtime(time.time()))
            np.save( LARecordFile, LARecord )
            np.save( LURecordFile, LURecord )
        
        print( "finish!" )



if __name__ == '__main__':

    parser  = argparse.ArgumentParser( description="deep MTL based activity and user recognition using wearable sensors" )
    
    parser.add_argument('-v', '--version',      type=str,       default = ""    )
    parser.add_argument('-g', '--gpu',          type=int,       default = 0     )
    parser.add_argument('-f', '--fold',         type=int,       default = 0     )
    parser.add_argument('-s', '--save_dir',     type=str.lower, default = 'test')
    parser.add_argument('-m', '--model',        type=int,       default = 1,        choices = [ 1, 2 ]  ) # 1: pretrain, 2: train
    
    args    = parser.parse_args()
    dataset = data_loader.UNIMIB()

    myModel = my_model( args.version, args.gpu, args.fold, args.save_dir, dataset, args.model )

    myModel.load_data()
    myModel.build_model()
    myModel.run_model()
