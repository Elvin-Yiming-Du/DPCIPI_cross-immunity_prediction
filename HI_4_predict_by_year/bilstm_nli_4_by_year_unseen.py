import json
import sys
sys.path.append("..")
from src.transformers import BertModel
from BiLSTM_NLI_Model import BiLSTM_NLI
from HI_4_predict_by_year.utils import shuffle_dataset
import matplotlib.pyplot as plt
import random
import numpy as np
import torch
import csv
import pandas as pd
from torch import nn
from torch.utils.data import DataLoader
from torch.utils.data import TensorDataset
from sklearn.metrics import confusion_matrix
from kmer_dict import Kmer_Dict

from sklearn.metrics import classification_report

device = torch.device("cuda:3" if torch.cuda.is_available() else "cpu")
print(device)
#定义随机种子，用于数据集的抽样
random_seed = 42

#获取到每一个基因与模板基因的开始位置的距离。
# 模板基因序列为   A T C T T T T A A
#       一个基因       C T T T A A A
#diff_position 即为2。
#diff_pos存储的所有基因序列与最长基因序列的开始位置的距离
def getDiffDic():
    with open("../diff_pos.json", 'r', encoding='UTF-8') as f:
        reduce_dict = json.load(f)
        return reduce_dict


# we fix the seeds to get consistent results before every training
# loop in what follows
def fix_seed(seed=234):
  torch.manual_seed(seed)
  torch.cuda.manual_seed(seed)
  np.random.seed(seed)
  random.seed(seed)

#获取句子的embedding表示
def getSeqEmbedding(seq):
    sentence_embedding = torch.ones_like(word_embeddings_dic["[PAD]"])
    for kmer in seq.split(' '):
        if kmer in word_embeddings_dic.keys():
            sentence_embedding = word_embeddings_dic[kmer] + sentence_embedding
        else:
            sentence_embedding = sentence_embedding + torch.ones_like(word_embeddings_dic['[PAD]'])

    sentence_embedding = 1/len(seq.split(' '))*sentence_embedding
    return sentence_embedding

#word2idx
def get_kmer2idx(vocab):
    word2idx = {w: idx for (idx, w) in enumerate(vocab)}
    return word2idx

#返回kmer列表，形如[[seq1, seq2, label],...]的列表
def getKmer(data_path):
    kmer_pairs = []
    with open(data_path, "r", encoding="utf-8") as r:
        kmer_data = r.readlines()
        i = 0
        for kmer in kmer_data:
            if i == 0 :
                i = i + 1
                continue
            tripules = kmer.split("\t")
            seq1 = tripules[0]
            seq2 = tripules[1]
            label = tripules[2]
            virus_1 = tripules[3]
            virus_2 = tripules[4]
            kmer_pairs.append([seq1, seq2, label, virus_1, virus_2])
    return kmer_pairs

#返回kmer的词向量词典
def get_word_embedding_dict(vocab_path, weight_parameter):
    idx2emb_dict = {}
    with open(vocab_path, "r", encoding="utf-8") as vocab:
        vocabulary = vocab.readlines()
        words = []
        for word in vocabulary:
            words.append(word.replace("\n", ""))

    word_embedding_dic = {}
    for word_index in range(len(words)):
        word_embedding_dic[words[word_index]] = weight_parameter[word_index]
        idx2emb_dict[word_index] = weight_parameter[word_index]
    return word_embedding_dic, idx2emb_dict

#获取Kmer字典
def getKmerVocab(word_embeddings_dic):
    vocab = word_embeddings_dic.keys()
    return vocab, len(vocab)

def findDiff(seq1, seq2):
    len1 = len(seq1)
    len2 = len(seq2)
    if len1 < len2:
        temp = seq2
        seq2 = seq1
        seq1 = temp
        tem = len1
        len2 = len1
        len1 = tem
    same_kmer = 0
    for i in range(len2):
        if seq1[i] == seq2[i]:
            same_kmer = same_kmer + 1
    diff_kmer = len1 - same_kmer
    return same_kmer, diff_kmer

def diff_seq(seq1, seq2, virus1, virus2):
    seq1_list = seq1.split(" ")
    seq2_list = seq2.split(" ")
    diff_pos_1 = diff_dict[virus1]
    diff_pos_2 = diff_dict[virus2]
    pad_seq1_list = ["#"]*diff_pos_1 + seq1_list
    pad_seq2_list = ["*"]*diff_pos_2 + seq2_list

    new_seq1 = []
    new_seq2 = []
    min_len = min(len(pad_seq1_list), len(pad_seq2_list))
    for i in range(min_len):
        if pad_seq1_list[i] != pad_seq2_list[i]:
            new_seq1.append(pad_seq1_list[i])
            new_seq2.append(pad_seq2_list[i])
    if len(pad_seq1_list) > min_len:
        new_seq1 = new_seq1 + seq1_list[min_len:]
    if len(pad_seq2_list) > min_len:
        new_seq2 = new_seq2 + seq2_list[min_len:]
    new_seq1_str = " ".join(new_seq1)
    new_seq2_str = " ".join(new_seq2)
    new_seq1_str = new_seq1_str.replace("# ","")
    new_seq2_str = new_seq2_str.replace("# ","")

    return new_seq1_str, new_seq2_str

def kmerseq2ids(kmer_dict, seq):
    idxs = np.zeros(len(seq.split(" ")))
    for index, kmer in enumerate(seq.split(" ")):
        if kmer in kmer_dict.vocab:
            idxs[index] = kmer_dict.get_kmer2idx()[kmer]
        else:
            kmer_dict.add_new_kmer(kmer)
            idxs[index]  = kmer_dict.get_kmer2idx()[kmer]
    return idxs

def virus_year(virus_year_data_path):
    csvFile = open(virus_year_data_path, "r")
    reader = csv.reader(csvFile)

    # 建立空字典
    virus_year = {}
    for item in reader:
        # 忽略第一行
        if reader.line_num == 1:
            continue
        virus_year[item[0].strip("\n")] = int(item[1])
    return virus_year


def getSequenceInputs(kmer_data):
    train_data = []
    test_data = []
    train_labels = []
    test_labels = []
    virus_year_dict = virus_year("../virus_year.csv")

    positive = 0
    negative = 0
    for triple in kmer_data:
        if int(triple[2].replace('\n','')) == 1:
            positive = positive + 1
        else:
            negative = negative + 1

    n_index = 0
    for triple in kmer_data:
        # if int(triple[2].replace('\n','')) != 1:
        #     n_index = n_index + 1
        #     if n_index < negative - positive:
        #         continue
        seq1 = triple[0].upper()
        seq2 = triple[1].upper()
        virus1 = triple[3]
        virus2 = triple[4].replace("\n","")
        # print(len(seq1))
        seq1, seq2 = diff_seq(seq1, seq2, virus1, virus2)
        # print(len(seq1))
        seq1_idxs = kmerseq2ids(kmer_dict, seq1)
        seq2_idxs = kmerseq2ids(kmer_dict, seq2)
        if len(seq1_idxs) > 1024:
            seq1_idxs = seq1_idxs[:1024]
        else:
            for i in range(0, 1024 - len(seq1_idxs)):
                seq1_idxs = np.append(seq1_idxs, 0)

        if len(seq2_idxs) > 1024:
            seq2_idxs = seq2_idxs[:1024]
        else:
            for i in range(0, 1024 - len(seq2_idxs)):
                seq2_idxs = np.append(seq2_idxs, 0)

        seq_idxs = torch.cat([torch.Tensor(seq1_idxs), torch.Tensor(seq2_idxs)], 0)

        if virus_year_dict[virus1] < 1996 and virus_year_dict[virus2] < 1996:
            train_data.append(seq_idxs)
            train_labels.append(float(triple[2].replace('\n','')))
        else:
            test_data.append(seq_idxs)
            test_labels.append(float(triple[2].replace('\n','')))

    print("dataset_distribution")
    print(positive)
    print(negative)
    print("train dataset size:")
    print(len(train_data))
    print("test dataset size:")
    print(len(test_data))
    return train_data, train_labels, test_data, test_labels


def getUnseenSequenceInputs(kmer_data):
    unseen_data = []
    test_data = []
    unseen_empty_labels = []
    virus_year_dict = virus_year("../virus_year.csv")
    n_index = 0
    for triple in kmer_data:
        seq1 = triple[0].upper()
        seq2 = triple[1].upper()
        virus1 = triple[3]
        virus2 = triple[4].replace("\n","")
        # print(len(seq1))
        seq1, seq2 = diff_seq(seq1, seq2, virus1, virus2)
        # print(len(seq1))
        seq1_idxs = kmerseq2ids(kmer_dict, seq1)
        seq2_idxs = kmerseq2ids(kmer_dict, seq2)
        if len(seq1_idxs) > 1024:
            seq1_idxs = seq1_idxs[:1024]
        else:
            for i in range(0, 1024 - len(seq1_idxs)):
                seq1_idxs = np.append(seq1_idxs, 0)

        if len(seq2_idxs) > 1024:
            seq2_idxs = seq2_idxs[:1024]
        else:
            for i in range(0, 1024 - len(seq2_idxs)):
                seq2_idxs = np.append(seq2_idxs, 0)

        seq_idxs = torch.cat([torch.Tensor(seq1_idxs), torch.Tensor(seq2_idxs)], 0)
        unseen_data.append(seq_idxs)
        unseen_empty_labels.append(float(0))
    return unseen_data, unseen_empty_labels

def train(train_loader, BiLSTM_NLI_model):
    BiLSTM_NLI_model.train()
    epochs = 51
    f1 = 0.0
    acc = 0.0
    recall = 0.0
    pre = 0.0

    for epoch in range(1, epochs):
        n_correct = 0
        n_total = 0
        true_correct = 0
        true_res = []
        pre_res = []
        for batch_idx, batch in enumerate(train_loader):

            sep_batch = torch.split(batch[0], 1024, dim=1)

            input1_lengths = (sep_batch[0] != 0).long().sum(dim=1)
            input2_lengths = (sep_batch[1] != 0).long().sum(dim=1)

            input1_length = max(input1_lengths)
            input2_length = max(input2_lengths)

            features1 = sep_batch[0][:, :input1_length]
            features2 = sep_batch[1][:, :input2_length]

            true_correct += batch[1].sum().item()
            optimizer.zero_grad()

            predictions = BiLSTM_NLI_model(features1, features2).squeeze(1)
            correct_batch = batch[1].long().to(device)
            loss = loss_fn(predictions, correct_batch)
            if epoch % 100 == 0 or epoch == epochs - 1:
                answer = nn.Sigmoid()(predictions)
                predict_batch = np.argmax(answer.detach().cpu().numpy(), axis=1)
                # correct_batch = np.argmin(batch[1].numpy(), axis=1)
                predict_batch = torch.from_numpy(predict_batch)
                zero_tensor = torch.zeros(predict_batch.size())
                ones_tensor = torch.ones(predict_batch.size())
                n_correct += torch.where(predict_batch == batch[1], ones_tensor, zero_tensor).sum().item()
                n_total = n_total + batch_size
                true_res.extend(batch[1].cpu().numpy())
                pre_res.extend(predict_batch.cpu().numpy())
            loss.backward()
            # update the parameters using the gradients and optimizer algorithm
            optimizer.step()


        if epoch % 100 == 0 or epoch == epochs - 1:
            print("-------------train------------")
            print(classification_report(true_res, pre_res, digits=4))
            print("-----------------------------------")

def val(validation_loader, BiLSTM_NLI_model):
    BiLSTM_NLI_model.eval()
    true_correct = 0
    n_correct, n_total, n_loss = 0, 0, 0
    f1 = 0.0
    acc = 0.0
    recall = 0.0
    pre = 0.0
    true_res = []
    pre_res = []
    with torch.no_grad():
        for batch_idx, batch in enumerate(validation_loader):
            true_correct += batch[1].sum().item()
            sep_batch = torch.split(batch[0], 1024, dim=1)

            input1_lengths = (sep_batch[0] != 0).long().sum(dim=1)
            input2_lengths = (sep_batch[1] != 0).long().sum(dim=1)

            input1_length = max(input1_lengths)
            input2_length = max(input2_lengths)

            features1 = sep_batch[0][:, :input1_length]
            features2 = sep_batch[1][:, :input2_length]

            pre = BiLSTM_NLI_model(features1, features2).squeeze(1)
            correct_batch = batch[1].long().to(device)
            loss = loss_fn(pre, correct_batch)
            answer = nn.Sigmoid()(pre)
            predict_batch = np.argmax(answer.cpu().numpy(), axis=1)
            predict_batch = torch.from_numpy(predict_batch)
            zero_tensor = torch.zeros(predict_batch.size())
            ones_tensor = torch.ones(predict_batch.size())
            n_correct += torch.where(predict_batch == batch[1], ones_tensor, zero_tensor).sum().item()
            n_total = n_total + batch_size
            n_loss += loss.item()
            true_res.extend(batch[1].cpu().numpy())
            pre_res.extend(predict_batch.cpu().numpy())
    print("--------val------------")
    print(classification_report(true_res, pre_res, digits=4))
    np.savetxt('bilstm_nli_result', (true_res, pre_res))
    plt.matshow(confusion_matrix(true_res, pre_res))
    plt.title('Confusion Matrix')
    plt.colorbar()
    plt.ylabel('Actural HI Degree') #实际类型
    plt.xlabel('Predict HI Degree') #预测类型
    # plt.show()
    plt.savefig('multi_label_classification.png')
    print("-----------------------")
    return n_loss/n_total
    
def predict(unseen_data_loader, BiLSTM_model):
    BiLSTM_model.eval()
    true_correct = 0
    n_correct, n_total, n_loss = 0, 0, 0
    f1 = 0.0
    acc = 0.0
    recall = 0.0
    pre = 0.0
    true_res = []
    pre_res = []
    print("we are testing unseen data sample")
    with torch.no_grad():
        for batch_idx, batch in enumerate(unseen_data_loader):
            true_correct += batch[1].sum().item()
            sep_batch = torch.split(batch[0], 1024, dim=1)

            input1_lengths = (sep_batch[0] != 0).long().sum(dim=1)
            input2_lengths = (sep_batch[1] != 0).long().sum(dim=1)

            input1_length = max(input1_lengths)
            input2_length = max(input2_lengths)

            features1 = sep_batch[0][:, :input1_length]
            features2 = sep_batch[1][:, :input2_length]

            pre = BiLSTM_NLI_model(features1, features2).squeeze(1)
            answer = nn.Sigmoid()(pre)
            answer = answer.to("cpu")
            predict_batch = np.argmax(answer.cpu().numpy(), axis=1)
            pre_res.extend(predict_batch)
            print(predict_batch)
            '''
            predict_batch = torch.from_numpy(predict_batch)
            zero_tensor = torch.zeros(answer.size())
            ones_tensor = torch.ones(answer.size())
            binary_answer = torch.where(answer > 0.5, ones_tensor, zero_tensor)
            '''
            
            '''
            loss = loss_fn(answer, batch[1])
            
            n_correct += torch.where(binary_answer == batch[1], ones_tensor, zero_tensor).sum().item()
            n_total += batch_size
            n_loss += loss.item()

            true_res.extend(batch[1].cpu().numpy())
            
            '''
    print(pre_res)
    print(len(pre_res))
    return pre_res

if __name__ == '__main__':
    # 获取DNABERT模型的embedding表示
    model = BertModel.from_pretrained("../dnabert")
    embeddings = model.get_input_embeddings()
    weight_parameter = embeddings.weight.data
    vocab_path = "../dnabert/vocab.txt"
    data_path = "../data_constructor/VHID_4_degree.tsv"
    unseen_data_path = "../VHID-unseen/VHID_unseen.tsv"
    # 获取embedding字典word_embeddings_dic，举例，key为AAATTC，value为768维的tensor;idx2emb_dic,key为AAATTC，value为768维的tensor
    kmer_dict = Kmer_Dict()
    kmer_dict.create_embedding(vocab_path, weight_parameter)

    # 获取数据data [[seq1,seq2,label],...],seq1指的是ATTCCG AATCCT ...,seq2指的是ATTCCT ATTCCG
    kmer_pairs = getKmer(data_path)
    unseen_kmer_pairs = getKmer(unseen_data_path)
    print("data总量：")
    print(len(kmer_pairs))
    # 获取到所有基因序列与最长基因序列的开始位置的距离字典
    diff_dict = getDiffDic()

    word_embeddings_dic = {}

    train_data, train_labels, test_data, test_labels = getSequenceInputs(kmer_pairs)
    unseen_data, unseen_labels = getUnseenSequenceInputs(unseen_kmer_pairs)
    vocab = kmer_dict.get_vocab()
    embedding_matrix = torch.Tensor([item.detach().numpy() for item in kmer_dict.word_embeddings_dic.values()])

    train_dataset = TensorDataset(torch.Tensor([item.detach().numpy() for item in train_data]),
                                   torch.Tensor([item for item in train_labels]))
    test_dataset = TensorDataset(torch.Tensor([item.detach().numpy() for item in test_data]),
                                   torch.Tensor([item for item in test_labels]))
                                   
    unseen_dataset = TensorDataset(torch.Tensor([item.detach().numpy() for item in unseen_data]),
                                   torch.Tensor([item for item in unseen_labels]))
                                   
    dataset_size = len(train_dataset)+len(test_dataset)

    batch_size = 10

    train_loader = shuffle_dataset(train_dataset, batch_size)
    validation_loader = shuffle_dataset(test_dataset, batch_size)
    unseen_data_loader = DataLoader(unseen_dataset, batch_size, shuffle=False)
    EMBEDDING_DIM = 768
    OUT_CHANNEL = 1
    WINDOW_SIZE = 2
    OUT_DIM = 4
    DROP_OUT = 0.1
    HIDDEN_SIZE = 100
    BiLSTM_NLI_model = BiLSTM_NLI(len(kmer_dict.vocab), EMBEDDING_DIM, HIDDEN_SIZE, DROP_OUT, embedding_matrix, OUT_DIM)
    BiLSTM_NLI_model.to(device)
    loss_fn = nn.CrossEntropyLoss()
    loss_fn = loss_fn.to(device)
    optimizer = torch.optim.Adam(BiLSTM_NLI_model.parameters(), lr=0.0001)
    scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[100, 200, 250], gamma=0.333,
                                                     last_epoch=-1)

    train(train_loader, BiLSTM_NLI_model)
    val_loss = val(validation_loader, BiLSTM_NLI_model)
    predict_rsult = predict(unseen_data_loader, BiLSTM_NLI_model)
    pd.DataFrame(predict_rsult).to_csv('unseen_multi_predict.csv')
    print("------------------val loss----------------")
    print(val_loss)




