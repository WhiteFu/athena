# Copyright (C) ATHENA AUTHORS
# All rights reserved.
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
# ==============================================================================
# Only support eager mode
# pylint: disable=invalid-name
r""" check point manager """
import os
import tensorflow as tf
from absl import logging
import numpy as np

class Checkpoint(tf.train.Checkpoint):
    """ A wrapper for Tensorflow checkpoint

    Args:
        checkpoint_directory: the directory for checkpoint
        summary_directory: the directory for summary used in Tensorboard
        __init__ provide the optimizer and model
        __call__ save the model

    Example:
        transformer = SpeechTransformer(target_vocab_size=dataset_builder.target_dim)
        optimizer = tf.keras.optimizers.Adam()
        ckpt = Checkpoint(checkpoint_directory='./train', summary_directory='./event',
            transformer=transformer, optimizer=optimizer)
        solver = BaseSolver(transformer)
        for epoch in dataset:
            ckpt()
    """

    def __init__(self, checkpoint_directory=None, metric_name=None, n_best_num=1, **kwargs):
        super().__init__(**kwargs)
        self.best_loss = np.inf
        self.n_best_model = {}
        self.n_best_num = n_best_num
        self.metric_name = metric_name
        if checkpoint_directory is None:
            checkpoint_directory = os.path.join(os.path.expanduser("~"), ".athena")
        self.checkpoint_prefix = os.path.join(checkpoint_directory, "ckpt")
        self.checkpoint_directory = checkpoint_directory
        logging.info("trying to restore from : %s" % checkpoint_directory)
        # load from checkpoint if previous models exist in checkpoint dir
        self.restore(tf.train.latest_checkpoint(checkpoint_directory))
        if os.path.exists(os.path.join(self.checkpoint_directory, 'n_best')):
            with open(os.path.join(self.checkpoint_directory, 'n_best')) as f:
                for line in f:
                    key, val = line.split('\t')
                    self.n_best_model[key] = float(val.strip())

    def _compare_and_save_best(self, loss, metrics, save_path):
        """ compare and save the best model in best_loss """
        checkpoint = save_path.split('/')[-1]
        if loss is not None and loss < self.best_loss:
            self.best_loss = loss
            with open(os.path.join(self.checkpoint_directory, 'best_loss'), 'w') as wf:
                wf.write('model_checkpoint_path: "%s"' % checkpoint)
        if metrics is None or len(metrics) == 0 or self.metric_name is None:
            return
        result = metrics[self.metric_name]
        n_best_value = np.array(list(self.n_best_model.values()))
        if len(n_best_value) < self.n_best_num:
            self.n_best_model[checkpoint] = result
        else:
            min_result = np.min(n_best_value)
            if result <= min_result:
                return
            min_index = np.argmin(n_best_value)
            min_key = list(self.n_best_model.keys())[min_index]
            self.n_best_model.pop(min_key)
            self.n_best_model[checkpoint] = result
        with open(os.path.join(self.checkpoint_directory, 'n_best'), 'w') as wf:
            for key in self.n_best_model:
                wf.write('%s\t%s\n' % (key, float(self.n_best_model[key])))

    def compute_nbest_avg(self, model_avg_num):
        """ restore n-best avg checkpoint """
        avg_file = os.path.join(self.checkpoint_directory, 'n_best')
        if not os.path.exists(avg_file):
            self.restore_from_best()
            return
        ckpt_metrics_dict = {}
        with open(avg_file) as f:
            for line in f:
                key, val = line.split('\t')
                ckpt_metrics_dict[key] = float(val.strip())
        ckpt_sorted_list = sorted(ckpt_metrics_dict.items(), key=lambda item: item[1], reverse=True)
        ckpt_list = ckpt_sorted_list[0: model_avg_num]
        ckpt_list = [k for k, _ in ckpt_list]
        logging.info('n_best_metrics_checkpoint: %s' % ckpt_list)
        ckpt_v_list = []
        # restore v from ckpts
        for key in ckpt_list:
            ckpt_path = os.path.join(self.checkpoint_directory, key)
            self.restore(ckpt_path)  # current variables will be updated
            var_list = []
            for i in self.model.trainable_variables:
                v = tf.constant(i.value())
                var_list.append(v)
            ckpt_v_list.append(var_list)
        # compute average, and assign to current variables
        for i in range(len(self.model.trainable_variables)):
            v = [tf.expand_dims(ckpt_v_list[j][i], [0]) for j in range(len(ckpt_v_list))]
            v = tf.reduce_mean(tf.concat(v, axis=0), axis=0)
            self.model.trainable_variables[i].assign(v)

    def __call__(self, loss=None, metrics=None):
        logging.info("saving model in :%s" % self.checkpoint_prefix)
        save_path = self.save(file_prefix=self.checkpoint_prefix)
        self._compare_and_save_best(loss, metrics, save_path)

    def restore_from_best(self):
        """ restore from the best model """
        self.restore(
            tf.train.latest_checkpoint(
                self.checkpoint_directory,
                latest_filename='best_loss'
            )
        )
