from layers import Position_Embedding,Attention_Layer,Self_Attention_Layer,Gate_Add_Lyaer,seq_and_vec,MaskedConv1D,MaskedLSTM
from keras.models import Model
from keras.layers import *
import keras
from keras_contrib.layers import CRF
from keras_multi_head import MultiHead
from keras_self_attention import SeqSelfAttention as self_attention

class base_model():
    def __init__(self,hidden_size,embed_size,vocab_size,multi_layers,maxlen,num_classes_part1,num_classes_part2,
                 learning_rate = 5e-5,embedding_dropout_prob=0.1,nn_dropout_prob=0.1,optmizer='adam'):
        """
        base multi-head selection joint extracton model
        :param hidden_size:
        :param embed_size:
        :param vocab_size:
        :param dropout_prob:
        """
        self.num_classes_part1 = num_classes_part1
        self.num_classes_part2 = num_classes_part2
        self.hidden_size = hidden_size
        self.embed_size = embed_size
        self.vocab_size = vocab_size
        self.maxlen = maxlen
        self.multi_layers = multi_layers
        self.learning_rate = learning_rate
        self.embedding_dropout_prob = embedding_dropout_prob
        self.nn_dropout_prob = nn_dropout_prob

    def model(self):
        """
        后面加上mask
        part1 : word_embed=>LSTM*2=>attention=>dense=>outputs
        part2 : outputs_concat LSTM => dense=>outputs
        :return:
        """
        word_input = Input(shape=(self.maxlen,))
        outputs_part1 = Input(shape=(self.maxlen,))
        outputs_part2 = Input(shape=(self.maxlen,))
        #word_embedding_layer
        word_embedding = Embedding(self.vocab_size,self.embed_size,name='word_embedding')(word_input)

        if self.embedding_dropout_prob:
            word_embedding = Dropout(self.embedding_dropout_prob)(word_embedding)

        lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='lstm_layer0'))(word_embedding)
        if self.nn_dropout_prob:
            lstm = Dropout(self.nn_dropout_prob)(lstm)
        #multi_lstm_layers
        if self.multi_layers >= 2:
            for i in range(self.multi_layers -1 ):
                lstm = Bidirectional(CuDNNLSTM(self.hidden_size//2,return_sequences=True,name='lstm_layer{}'.format(i+1)))(lstm)
                if self.nn_dropout_prob:
                    lstm = Dropout(self.nn_dropout_prob)(lstm)

        attention = TimeDistributed(Dense(1, activation='tanh'))(lstm)
        attention = Flatten()(attention)
        attention = Activation('softmax')(attention)
        attention = RepeatVector(self.hidden_size)(attention)
        attention = Permute([2, 1])(attention)
        sent_representation = multiply([lstm, attention])
        attention = Lambda(lambda xin: K.sum(xin, axis=1))(sent_representation)
        lstm_attention = Lambda(seq_and_vec, output_shape=(None, self.hidden_size * 2))([lstm, attention]) #[这里考虑下用相加的方法，以及门控相加]
        entity_pred = Dense(self.num_classes_part1,activation='softmax')(lstm_attention)
        entity_model = Model([word_input],[entity_pred])

        ###############################################################################################################################
        #part2 multi-head selection for relation classfication
        h = Concatenate(axis=-1)([lstm,entity_pred])
        multi_head_selection_pred = Dense(self.num_classes_part2,activation='softmax')(h)
        relation_model = Model([word_input],[multi_head_selection_pred])
        train_model = Model([word_input,outputs_part1,outputs_part2],[multi_head_selection_pred])

        part1_loss = K.sparse_categorical_crossentropy(outputs_part1,entity_pred)
        part2_loss = K.sparse_categorical_crossentropy(outputs_part2,multi_head_selection_pred)
        part1_loss = K.mean(part1_loss)
        part2_loss = K.mean(part2_loss)

        train_model.add_loss(part1_loss+part2_loss)
        train_model.compile(keras.optimizers.adam(lr=5e-5))

        return entity_model,relation_model,train_model

class word_char_model_1():
    def __init__(self,hidden_size,word_embed_size,char_embed_size,word_vocab_size,char_vocab_size,multi_layers,maxlen,maxlen_word,num_classes_part1,
                 num_classes_part2,learning_rate = 5e-5,embedding_dropout_prob=0.1,nn_dropout_prob=0.1,optmizer='adam'):
        """
        try the concatenate way for word_char_embedding，char_embedding from the char-level of word maxpooling

        :param hidden_size:
        :param embed_size:
        :param vocab_size:
        :param dropout_prob:
        """
        self.num_classes_part1 = num_classes_part1
        self.num_classes_part2 = num_classes_part2
        self.hidden_size = hidden_size
        self.word_embed_size = word_embed_size
        self.char_embed_size = char_embed_size
        self.word_vocab_size = word_vocab_size
        self.char_vocab_size = char_vocab_size
        self.maxlen = maxlen
        self.maxlen_word = maxlen_word
        self.multi_layers = multi_layers
        self.learning_rate = learning_rate
        self.embedding_dropout_prob = embedding_dropout_prob
        self.nn_dropout_prob = nn_dropout_prob

    def model(self):
        """
        后面加上mask
        part1 : word_embed=>LSTM*2=>attention=>dense=>outputs
        part2 : outputs_concat LSTM => dense=>outputs
        :return:
        """
        word_input = Input(shape=(self.maxlen,)) #[batch_size,sentence]
        char_input = Input(shape=(self.maxlen,self.maxlen_word,)) #[batch_size,sentence,word]
        outputs_part1 = Input(shape=(self.maxlen,))
        outputs_part2 = Input(shape=(self.maxlen,))
        #word_embedding_layer
        word_embedding = Embedding(self.word_vocab_size,self.word_embed_size,name='word_embedding')(word_input)
        char_embedding = Embedding(self.char_vocab_size,self.char_embed_size,name='char_embedding')(char_input) #[batch,sentence,word,dim of char embedding]
        if self.embedding_dropout_prob:
            word_embedding = Dropout(self.embedding_dropout_prob)(word_embedding)
            char_embedding = Dropout(self.embedding_dropout_prob)(char_embedding)

        #char_embedding maxpooling part
        char_embedding_shape = K.int_shape(char_embedding)#[batch,sentence,word,dim]
        char_embedding_reshaped = K.reshape(char_embedding,shape=[-1,char_embedding_shape[-2],self.char_embed_size]) #[batch*sentence,word,dim of char embedding]
        char_lstm = Bidirectional(CuDNNLSTM(self.hidden_size//2,return_sequences=True,name='char_lstm_layer'))(char_embedding_reshaped)
        char_maxpool = GlobalMaxPooling1D(char_lstm) #[batch*sentence,hidden_size]
        char_embedding = K.reshape(char_maxpool,shape=[-1,char_embedding_shape[1],self.hidden_size]) #[batch,sentence,hidden_size]
        embedding = Concatenate(axis=-1)([word_embedding,char_embedding])

        #part1 , entity_pred
        lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='lstm_layer0'))(embedding)
        if self.nn_dropout_prob:
            lstm = Dropout(self.nn_dropout_prob)(lstm)
        #multi_lstm_layers
        if self.multi_layers >= 2:
            for i in range(self.multi_layers -1 ):
                lstm = Bidirectional(CuDNNLSTM(self.hidden_size//2,return_sequences=True,name='lstm_layer{}'.format(i+1)))(lstm)
                if self.nn_dropout_prob:
                    lstm = Dropout(self.nn_dropout_prob)(lstm)

        attention = TimeDistributed(Dense(1, activation='tanh'))(lstm)
        attention = Flatten()(attention)
        attention = Activation('softmax')(attention)
        attention = RepeatVector(self.hidden_size)(attention)
        attention = Permute([2, 1])(attention)
        sent_representation = multiply([lstm, attention])
        attention = Lambda(lambda xin: K.sum(xin, axis=1))(sent_representation)
        lstm_attention = Lambda(seq_and_vec, output_shape=(None, self.hidden_size * 2))([lstm, attention]) #[这里考虑下用相加的方法，以及门控相加]
        entity_pred = Dense(self.num_classes_part1,activation='softmax')(lstm_attention)
        entity_model = Model([word_input],[entity_pred])

        ###############################################################################################################################
        #part2 multi-head selection for relation classfication
        h = Concatenate(axis=-1)([lstm,entity_pred])
        multi_head_selection_pred = Dense(self.num_classes_part2,activation='softmax')(h)
        relation_model = Model([word_input],[multi_head_selection_pred])
        train_model = Model([word_input,outputs_part1,outputs_part2],[multi_head_selection_pred])

        part1_loss = K.sparse_categorical_crossentropy(outputs_part1,entity_pred)
        part2_loss = K.sparse_categorical_crossentropy(outputs_part2,multi_head_selection_pred)
        part1_loss = K.mean(part1_loss)
        part2_loss = K.mean(part2_loss)

        train_model.add_loss(part1_loss+part2_loss)
        train_model.compile(keras.optimizers.adam(lr=5e-5))

        return entity_model,relation_model,train_model

class word_char_model_2():
    def __init__(self, hidden_size, word_embed_size, char_embed_size, word_vocab_size,char_vocab_size, multi_layers, maxlen, maxlen_word,
                 num_classes_part1,num_classes_part2, learning_rate=5e-5, embedding_dropout_prob=0.1,nn_dropout_prob=0.1, optmizer='adam'):
        """
        try the concatenate way for word_char_embedding，char_embedding from the char-level of word attention

        :param hidden_size:
        :param embed_size:
        :param vocab_size:
        :param dropout_prob:
        """
        self.num_classes_part1 = num_classes_part1
        self.num_classes_part2 = num_classes_part2
        self.hidden_size = hidden_size
        self.word_embed_size = word_embed_size
        self.char_embed_size = char_embed_size
        self.word_vocab_size = word_vocab_size
        self.char_vocab_size = char_vocab_size
        self.maxlen = maxlen
        self.maxlen_word = maxlen_word
        self.multi_layers = multi_layers
        self.learning_rate = learning_rate
        self.embedding_dropout_prob = embedding_dropout_prob
        self.nn_dropout_prob = nn_dropout_prob

    def model(self):
        """
        后面加上mask
        part1 : word_embed=>LSTM*2=>attention=>dense=>outputs
        part2 : outputs_concat LSTM => dense=>outputs
        :return:
        """
        word_input = Input(shape=(self.maxlen,))  # [batch_size,sentence]
        char_input = Input(shape=(self.maxlen, self.maxlen_word,))  # [batch_size,sentence,word]
        outputs_part1 = Input(shape=(self.maxlen,))
        outputs_part2 = Input(shape=(self.maxlen,))
        # word_embedding_layer
        word_embedding = Embedding(self.word_vocab_size, self.word_embed_size, name='word_embedding')(word_input)
        char_embedding = Embedding(self.char_vocab_size, self.char_embed_size, name='char_embedding')(
            char_input)  # [batch,sentence,word,dim of char embedding]
        if self.embedding_dropout_prob:
            word_embedding = Dropout(self.embedding_dropout_prob)(word_embedding)
            char_embedding = Dropout(self.embedding_dropout_prob)(char_embedding)

        # char_embedding maxpooling part
        char_embedding_shape = K.int_shape(char_embedding)  # [batch,sentence,word,dim]
        char_embedding_reshaped = K.reshape(char_embedding, shape=[-1, char_embedding_shape[-2],
                                                                   self.char_embed_size])  # [batch*sentence,word,dim of char embedding]
        char_lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='char_lstm_layer'))(
            char_embedding_reshaped)
        # char_maxpool = GlobalMaxPooling1D(char_lstm)  # [batch*sentence,hidden_size]
        char_att = Attention_Layer()(char_lstm) #[batch*sentence,hidden_size]
        # char_embedding = K.reshape(char_maxpool, shape=[-1, char_embedding_shape[1],
        #                                                 self.hidden_size])  # [batch,sentence,hidden_size]
        char_embedding = K.reshape(char_att,shape=[-1,char_embedding_shape[-1],self.hidden_size])#[batch,sentence,hidden_size]
        embedding = Concatenate(axis=-1)([word_embedding, char_embedding])

        # part1 , entity_pred
        lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='lstm_layer0'))(embedding)
        if self.nn_dropout_prob:
            lstm = Dropout(self.nn_dropout_prob)(lstm)
        # multi_lstm_layers
        if self.multi_layers >= 2:
            for i in range(self.multi_layers - 1):
                lstm = Bidirectional(
                    CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='lstm_layer{}'.format(i + 1)))(lstm)
                if self.nn_dropout_prob:
                    lstm = Dropout(self.nn_dropout_prob)(lstm)

        attention = TimeDistributed(Dense(1, activation='tanh'))(lstm)
        attention = Flatten()(attention)
        attention = Activation('softmax')(attention)
        attention = RepeatVector(self.hidden_size)(attention)
        attention = Permute([2, 1])(attention)
        sent_representation = multiply([lstm, attention])
        attention = Lambda(lambda xin: K.sum(xin, axis=1))(sent_representation)
        lstm_attention = Lambda(seq_and_vec, output_shape=(None, self.hidden_size * 2))(
            [lstm, attention])  # [这里考虑下用相加的方法，以及门控相加]
        entity_pred = Dense(self.num_classes_part1, activation='softmax')(lstm_attention)
        entity_model = Model([word_input], [entity_pred])

        ###############################################################################################################################
        # part2 multi-head selection for relation classfication
        h = Concatenate(axis=-1)([lstm, entity_pred])
        multi_head_selection_pred = Dense(self.num_classes_part2, activation='softmax')(h)
        relation_model = Model([word_input], [multi_head_selection_pred])
        train_model = Model([word_input, outputs_part1, outputs_part2], [multi_head_selection_pred])

        part1_loss = K.sparse_categorical_crossentropy(outputs_part1, entity_pred)
        part2_loss = K.sparse_categorical_crossentropy(outputs_part2, multi_head_selection_pred)
        part1_loss = K.mean(part1_loss)
        part2_loss = K.mean(part2_loss)

        train_model.add_loss(part1_loss + part2_loss)
        train_model.compile(keras.optimizers.adam(lr=5e-5))

        return entity_model, relation_model, train_model

class word_char_model_3():

    def __init__(self, hidden_size, word_embed_size, char_embed_size, word_vocab_size, char_vocab_size, multi_layers, maxlen, maxlen_word,
                 num_classes_part1, num_classes_part2, learning_rate=5e-5, embedding_dropout_prob=0.1, nn_dropout_prob=0.1, optmizer='adam'):
        """
        try the add way for word_char_embedding, char_embedding from the char-leve of word maxpool

        :param hidden_size:
        :param embed_size:
        :param vocab_size:
        :param dropout_prob:
        """
        self.num_classes_part1 = num_classes_part1
        self.num_classes_part2 = num_classes_part2
        self.hidden_size = hidden_size
        self.word_embed_size = word_embed_size
        self.char_embed_size = char_embed_size
        self.word_vocab_size = word_vocab_size
        self.char_vocab_size = char_vocab_size
        self.maxlen = maxlen
        self.maxlen_word = maxlen_word
        self.multi_layers = multi_layers
        self.learning_rate = learning_rate
        self.embedding_dropout_prob = embedding_dropout_prob
        self.nn_dropout_prob = nn_dropout_prob

    def model(self):
        """
        后面加上mask
        part1 : word_embed=>LSTM*2=>attention=>dense=>outputs
        part2 : outputs_concat LSTM => dense=>outputs
        :return:
        """

        word_input = Input(shape=(self.maxlen,))  # [batch_size,sentence]
        char_input = Input(shape=(self.maxlen, self.maxlen_word,))  # [batch_size,sentence,word]
        outputs_part1 = Input(shape=(self.maxlen,))
        outputs_part2 = Input(shape=(self.maxlen,))
        # word_embedding_layer
        word_embedding = Embedding(self.word_vocab_size, self.word_embed_size, name='word_embedding')(word_input)
        char_embedding = Embedding(self.char_vocab_size, self.char_embed_size, name='char_embedding')(char_input)  # [batch,sentence,word,dim of char embedding]
        if self.embedding_dropout_prob:
            word_embedding = Dropout(self.embedding_dropout_prob)(word_embedding)
            char_embedding = Dropout(self.embedding_dropout_prob)(char_embedding)

        # char_embedding maxpooling part
        char_embedding_shape = K.int_shape(char_embedding)  # [batch,sentence,word,dim]
        char_embedding_reshaped = K.reshape(char_embedding, shape=[-1, char_embedding_shape[-2],
                                                                   self.char_embed_size])  # [batch*sentence,word,dim of char embedding]
        char_lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='char_lstm_layer'))(
            char_embedding_reshaped)
        char_maxpool = GlobalMaxPooling1D(char_lstm)  # [batch*sentence,hidden_size]
        # char_att = Attention_Layer()(char_lstm) #[batch*sentence,hidden_size]
        char_embedding = K.reshape(char_maxpool, shape=[-1, char_embedding_shape[1],
                                                        self.hidden_size])  # [batch,sentence,hidden_size]
        # char_embedding = K.reshape(char_att,shape=[-1,char_embedding_shape[-1],self.hidden_size])#[batch,sentence,hidden_size]
        # embedding = Concatenate(axis=-1)([word_embedding, char_embedding])
        embedding = Gate_Add_Lyaer()([word_embedding, char_embedding])  # gate add ==> embedding
        # part1 , entity_pred
        lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='lstm_layer0'))(embedding)
        if self.nn_dropout_prob:
            lstm = Dropout(self.nn_dropout_prob)(lstm)
        # multi_lstm_layers
        if self.multi_layers >= 2:
            for i in range(self.multi_layers - 1):
                lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='lstm_layer{}'.format(i + 1)))(lstm)
                if self.nn_dropout_prob:
                    lstm = Dropout(self.nn_dropout_prob)(lstm)

        attention = TimeDistributed(Dense(1, activation='tanh'))(lstm)
        attention = Flatten()(attention)
        attention = Activation('softmax')(attention)
        attention = RepeatVector(self.hidden_size)(attention)
        attention = Permute([2, 1])(attention)
        sent_representation = multiply([lstm, attention])
        attention = Lambda(lambda xin: K.sum(xin, axis=1))(sent_representation)
        lstm_attention = Lambda(seq_and_vec, output_shape=(None, self.hidden_size * 2))(
            [lstm, attention])  # [这里考虑下用相加的方法，以及门控相加]
        entity_pred = Dense(self.num_classes_part1, activation='softmax')(lstm_attention)
        entity_model = Model([word_input], [entity_pred])

        ###############################################################################################################################
        # part2 multi-head selection for relation classfication
        h = Concatenate(axis=-1)([lstm, entity_pred])
        multi_head_selection_pred = Dense(self.num_classes_part2, activation='softmax')(h)
        relation_model = Model([word_input], [multi_head_selection_pred])
        train_model = Model([word_input, outputs_part1, outputs_part2], [multi_head_selection_pred])

        part1_loss = K.sparse_categorical_crossentropy(outputs_part1, entity_pred)
        part2_loss = K.sparse_categorical_crossentropy(outputs_part2, multi_head_selection_pred)
        part1_loss = K.mean(part1_loss)
        part2_loss = K.mean(part2_loss)

        train_model.add_loss(part1_loss + part2_loss)
        train_model.compile(keras.optimizers.adam(lr=5e-5))

        return entity_model, relation_model, train_model

class self_attention_base_model():

    def __init__(self,hidden_size,embed_size,vocab_size,multi_layers,maxlen,maxlen_word,num_classes_part1,
                 num_classes_part2,learning_rate = 5e-5,embedding_dropout_prob=0.1,nn_dropout_prob=0.1,optmizer='adam'):
        """
        try the gate add way for word_char_embedding and self-attention for part1 without word_char embedding

        :param hidden_size:
        :param embed_size:
        :param vocab_size:
        :param dropout_prob:
        """
        self.num_classes_part1 = num_classes_part1
        self.num_classes_part2 = num_classes_part2
        self.hidden_size = hidden_size
        self.embed_size = embed_size
        # self.pos_embed_size = pos_embed_size
        self.vocab_size = vocab_size
        self.maxlen = maxlen
        self.multi_layers = multi_layers
        self.learning_rate = learning_rate
        self.embedding_dropout_prob = embedding_dropout_prob
        self.nn_dropout_prob = nn_dropout_prob

    def model(self):
        """
        后面加上mask
        part1 : word_embed=>multi self attention layer
        part2 : outputs_concat LSTM => dense=>outputs
        :return:
        """
        word_input = Input(shape=(self.maxlen,))
        outputs_part1 = Input(shape=(self.maxlen,))
        outputs_part2 = Input(shape=(self.maxlen,))
        # word_embedding_layer
        word_embedding = Embedding(self.vocab_size, self.embed_size, name='word_embedding')(word_input)
        embedding = Position_Embedding(mode='sum')(word_embedding)

        if self.embedding_dropout_prob:
            embedding = Dropout(self.embedding_dropout_prob)(embedding)

        self_attention = Self_Attention_Layer(self.multi_layers,self.nn_dropout_prob)(embedding)

        entity_pred = Dense(self.num_classes_part1, activation='softmax')(self_attention)
        entity_model = Model([word_input], [entity_pred])
        ###############################################################################################################################
        # part2 multi-head selection for relation classfication
        h = Concatenate(axis=-1)([self_attention, entity_pred])
        multi_head_selection_pred = Dense(self.num_classes_part2, activation='softmax')(h)
        relation_model = Model([word_input], [multi_head_selection_pred])
        train_model = Model([word_input, outputs_part1, outputs_part2], [multi_head_selection_pred])

        part1_loss = K.sparse_categorical_crossentropy(outputs_part1, entity_pred)
        part2_loss = K.sparse_categorical_crossentropy(outputs_part2, multi_head_selection_pred)
        part1_loss = K.mean(part1_loss)
        part2_loss = K.mean(part2_loss)

        train_model.add_loss(part1_loss + part2_loss)
        train_model.compile(keras.optimizers.adam(lr=5e-5))

        return entity_model, relation_model, train_model


class self_attention_model_ner_part():
    def __init__(self,hidden_size,nb_head,word_embed_size,char_embed_size,word_vocab_size,char_vocab_size,multi_layers,num_classes
                 ,maxlen_sentence,maxlen_word,learning_rate = 5e-5,embedding_dropout_prob=0.1,nn_dropout_prob=0.1,optmizer='adam',
                 is_use_char_embedding=False):
        """
        测试一下self-attention在ner上的效果
        """
        self.num_classes = num_classes
        self.hidden_size = hidden_size
        self.nb_head = nb_head
        self.word_embed_size = word_embed_size
        self.char_embed_size = char_embed_size
        # self.pos_embed_size = pos_embed_size #use the add position_embedding
        self.word_vocab_size = word_vocab_size
        self.char_vocab_size = char_vocab_size
        # self.maxlen = maxlen
        self.multi_layers = multi_layers
        self.maxlen_sentence = maxlen_sentence
        self.maxlen_word = maxlen_word
        self.learning_rate = learning_rate
        self.embedding_dropout_prob = embedding_dropout_prob
        self.nn_dropout_prob = nn_dropout_prob
        self.is_use_char_embedding = is_use_char_embedding
        print(multi_layers)

    def model(self):
        word_input = Input(shape=(self.maxlen_sentence,)) #[batch,sentencen]
        char_input = Input(shape=(self.maxlen_sentence,self.maxlen_word,)) #[batch,word,char]
        ner_label = Input(shape=(self.maxlen_sentence,))
        word_embedding = Embedding(self.word_vocab_size, self.word_embed_size,name='word_embedding',mask_zero=True)(word_input) #[batch,word,embed]
        char_embedding = Embedding(self.char_vocab_size,self.char_embed_size,name='char_embedding',mask_zero=True)(char_input) #[batch,word,char,embedd]

        mask = Lambda(lambda x: K.cast(K.greater(K.expand_dims(x, 2), 0), 'float32'))(word_input)

        if self.embedding_dropout_prob:
            word_embedding = Dropout(self.embedding_dropout_prob)(word_embedding)
            char_embedding = Dropout(self.embedding_dropout_prob)(char_embedding)

        if self.is_use_char_embedding:
            # char_embedding maxpooling part
            char_embedding_shape = K.int_shape(char_embedding)  # [batch,sentence,word,dim]
            char_embedding_reshaped = K.reshape(char_embedding, shape=(-1, char_embedding_shape[-2],
                                                                       self.char_embed_size))  # [batch*sentence,word,dim of char embedding]

            char_lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='char_lstm_layer'))(
                char_embedding_reshaped)
            # char_maxpool = GlobalMaxPooling1D(char_lstm)  # [batch*sentence,hidden_size]
            char_att = Attention_Layer()(char_lstm)  # [batch*sentence,hidden_size]
            # char_embedding = K.reshape(char_maxpool, shape=[-1, char_embedding_shape[1],
            #                                                 self.hidden_size])  # [batch,sentence,hidden_size]
            char_embedding = K.reshape(char_att, shape=[-1, char_embedding_shape[-1], self.hidden_size])  # [batch,sentence,hidden_size]
            embedding = Gate_Add_Lyaer()([word_embedding,char_embedding])
        else:
            embedding = word_embedding
        #multi-layers self-attention for ner pred

        # part1 , multi-self-attentionblock, (CNN/LSTM/FNN+self-attention)
        # lstm = CuDNNLSTM(self.hidden_size,name='lstm0',return_sequences=True)(embedding)
        # self_att = Self_Attention_Layer(8,16,name='self-att0')([lstm,lstm,lstm])
        cnn = MaskedConv1D(filters=self.hidden_size,kernel_size=3,activation='relu',padding='same')(embedding)
        self_att = self_attention(units=self.hidden_size)(cnn)
        if self.nn_dropout_prob:
            self_att = Dropout(self.nn_dropout_prob)(self_att)
        # if self.multi_layers >=2 :
        #     for i in range(self.multi_layers-1):
        #         i = i+1
        #         lstm = CuDNNLSTM(self.hidden_size, name='lstm{}'.format(i), return_sequences=True)(self_att)
        #         self_att = Self_Attention_Layer(8, 16, name='self-att{}'.format(i))([lstm,lstm,lstm])
        #         self_att = Dense(self.hidden_size,name='dense{}'.format(i))(self_att)
        #         # self_att = BatchNormalization()(self_att)

        bio_pred = Dense(self.num_classes, activation='softmax')(self_att)
        pred_model =Model([word_input, char_input], bio_pred)
        train_model = Model([word_input, char_input, ner_label], bio_pred)

        loss = K.sparse_categorical_crossentropy(ner_label, bio_pred)
        loss = K.sum(loss * mask[:, :, 0]) / K.sum(mask)

        train_model.summary()
        train_model.add_loss(loss)
        train_model.compile(keras.optimizers.adam(lr=self.learning_rate))

        return train_model,pred_model

class lstm_model_ner_part():
    def __init__(self,hidden_size,nb_head,word_embed_size,char_embed_size,word_vocab_size,char_vocab_size,multi_layers,num_classes
                 ,maxlen_sentence,maxlen_word,learning_rate = 5e-5,embedding_dropout_prob=0.1,nn_dropout_prob=0.1,optmizer='adam',
                 is_use_char_embedding=False):
        """
        测试一下self-attention在ner上的效果
        """
        self.num_classes = num_classes
        self.hidden_size = hidden_size
        self.nb_head = nb_head
        self.word_embed_size = word_embed_size
        self.char_embed_size = char_embed_size
        # self.pos_embed_size = pos_embed_size #use the add position_embedding
        self.word_vocab_size = word_vocab_size
        self.char_vocab_size = char_vocab_size
        # self.maxlen = maxlen
        self.multi_layers = multi_layers
        self.maxlen_sentence = maxlen_sentence
        self.maxlen_word = maxlen_word
        self.learning_rate = learning_rate
        self.embedding_dropout_prob = embedding_dropout_prob
        self.nn_dropout_prob = nn_dropout_prob
        self.is_use_char_embedding = is_use_char_embedding
        print(multi_layers)

    def model(self):
        word_input = Input(shape=(self.maxlen_sentence,)) #[batch,sentencen]
        char_input = Input(shape=(self.maxlen_sentence,self.maxlen_word,)) #[batch,word,char]
        ner_label = Input(shape=(self.maxlen_sentence,))

        mask = Lambda(lambda x: K.cast(K.greater(K.expand_dims(x, 2), 0), 'float32'))(word_input)

        word_embedding = Embedding(self.word_vocab_size, self.word_embed_size,name='word_embedding',trainable=True)(word_input) #[batch,word,embed]
        char_embedding = Embedding(self.char_vocab_size,self.char_embed_size,name='char_embedding',trainable=True)(char_input) #[batch,word,char,embedd]

        if self.embedding_dropout_prob:
            word_embedding = Dropout(self.embedding_dropout_prob)(word_embedding)
            char_embedding = Dropout(self.embedding_dropout_prob)(char_embedding)

        if self.is_use_char_embedding:
            # char_embedding maxpooling part
            char_embedding_shape = K.int_shape(char_embedding)  # [batch,sentence,word,dim]
            char_embedding_reshaped = K.reshape(char_embedding, shape=(-1, char_embedding_shape[-2],
                                                                       self.char_embed_size))  # [batch*sentence,word,dim of char embedding]

            char_lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True, name='char_lstm_layer'))(
                char_embedding_reshaped)
            # char_maxpool = GlobalMaxPooling1D(char_lstm)  # [batch*sentence,hidden_size]
            char_att = Attention_Layer()(char_lstm)  # [batch*sentence,hidden_size]
            # char_embedding = K.reshape(char_maxpool, shape=[-1, char_embedding_shape[1],
            #                                                 self.hidden_size])  # [batch,sentence,hidden_size]
            char_embedding = K.reshape(char_att, shape=[-1, char_embedding_shape[-1], self.hidden_size])  # [batch,sentence,hidden_size]
            embedding = Gate_Add_Lyaer()([word_embedding,char_embedding])
        else:
            embedding = word_embedding
        #multi-layers self-attention for ner pred

        # part1 , multi-self-attentionblock, (CNN/LSTM/FNN+self-attention)
        lstm = Bidirectional(MaskedLSTM(units=self.hidden_size // 2, return_sequences=True), name='lstm_layer0')(embedding)
        if self.nn_dropout_prob:
            lstm = Dropout(self.nn_dropout_prob)(lstm)

        # # multi_lstm_layers
        # if self.multi_layers >= 2:
        #     for i in range(self.multi_layers - 1):
        #         i+=1
        #         lstm = Bidirectional(CuDNNLSTM(self.hidden_size // 2, return_sequences=True), name='lstm_layer{}'.format(i))(lstm)
        #         if self.nn_dropout_prob:
        #             lstm = Dropout(self.nn_dropout_prob)(lstm)

        # attention = TimeDistributed(Dense(1, activation='tanh'))(lstm)
        # attention = Flatten()(attention)
        # attention = Activation('softmax')(attention)
        # attention = RepeatVector(self.hidden_size)(attention)
        # attention = Permute([2, 1])(attention)
        # sent_representation = multiply([lstm, attention])
        # attention = Lambda(lambda xin: K.sum(xin, axis=1))(sent_representation)
        # lstm_attention = Lambda(seq_and_vec, output_shape=(None, self.hidden_size * 2))(
        #     [lstm, attention])  # [这里考虑下用相加的方法，以及门控相加]

        bio_pred = Dense(self.num_classes, activation='softmax')(lstm)

        pred_model =Model([word_input, char_input], bio_pred)
        train_model = Model([word_input, char_input, ner_label], bio_pred)

        loss = K.sparse_categorical_crossentropy(ner_label, bio_pred)
        loss = K.sum(loss * mask[:, :, 0]) / K.sum(mask)

        train_model.summary()
        train_model.add_loss(loss)
        train_model.compile(keras.optimizers.adam(lr=self.learning_rate))

        return train_model,pred_model
