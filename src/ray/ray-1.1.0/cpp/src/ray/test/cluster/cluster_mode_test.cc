
#include <gtest/gtest.h>
#include <ray/api.h>
#include <ray/api/ray_config.h>
#include <ray/experimental/default_worker.h>

using namespace ::ray::api;

/// general function of user code
int Return1() { return 1; }
int Plus1(int x) { return x + 1; }
int Plus(int x, int y) { return x + y; }

/// a class of user code
class Counter {
 public:
  int count;

  Counter(int init) { count = init; }
  static Counter *FactoryCreate() { return new Counter(0); }
  static Counter *FactoryCreate(int init) { return new Counter(init); }
  static Counter *FactoryCreate(int init1, int init2) {
    return new Counter(init1 + init2);
  }
  /// non static function
  int Plus1() {
    count += 1;
    return count;
  }
  int Add(int x) {
    count += x;
    return count;
  }
};

TEST(RayClusterModeTest, FullTest) {
  /// initialization to cluster mode
  ray::api::RayConfig::GetInstance()->run_mode = RunMode::CLUSTER;
  /// TODO(Guyang Song): add the dynamic library name
  ray::api::RayConfig::GetInstance()->lib_name = "";
  Ray::Init();

  /// put and get object
  auto obj = Ray::Put(12345);
  auto get_result = *(Ray::Get(obj));
  EXPECT_EQ(12345, get_result);

  /// common task without args
  auto task_obj = Ray::Task(Return1).Remote();
  int task_result = *(Ray::Get(task_obj));
  EXPECT_EQ(1, task_result);

  /// common task with args
  task_obj = Ray::Task(Plus1, 5).Remote();
  task_result = *(Ray::Get(task_obj));
  EXPECT_EQ(6, task_result);

  /// actor task without args
  ActorHandle<Counter> actor1 = Ray::Actor(Counter::FactoryCreate).Remote();
  auto actor_object1 = actor1.Task(&Counter::Plus1).Remote();
  int actor_task_result1 = *(Ray::Get(actor_object1));
  EXPECT_EQ(1, actor_task_result1);

  /// actor task with args
  ActorHandle<Counter> actor2 = Ray::Actor(Counter::FactoryCreate, 1).Remote();
  auto actor_object2 = actor2.Task(&Counter::Add, 5).Remote();
  int actor_task_result2 = *(Ray::Get(actor_object2));
  EXPECT_EQ(6, actor_task_result2);

  /// actor task with args which pass by reference
  ActorHandle<Counter> actor3 = Ray::Actor(Counter::FactoryCreate, 6, 0).Remote();
  auto actor_object3 = actor3.Task(&Counter::Add, actor_object2).Remote();
  int actor_task_result3 = *(Ray::Get(actor_object3));
  EXPECT_EQ(12, actor_task_result3);

  /// general function remote call???args passed by value???
  auto r0 = Ray::Task(Return1).Remote();
  auto r1 = Ray::Task(Plus1, 30).Remote();
  auto r2 = Ray::Task(Plus, 3, 22).Remote();

  int result1 = *(Ray::Get(r1));
  int result0 = *(Ray::Get(r0));
  int result2 = *(Ray::Get(r2));
  EXPECT_EQ(result0, 1);
  EXPECT_EQ(result1, 31);
  EXPECT_EQ(result2, 25);

  /// general function remote call???args passed by reference???
  auto r3 = Ray::Task(Return1).Remote();
  auto r4 = Ray::Task(Plus1, r3).Remote();
  auto r5 = Ray::Task(Plus, r4, r3).Remote();
  auto r6 = Ray::Task(Plus, r4, 10).Remote();

  ///// TODO(ameer/guyang): All the commented code lines below should be
  ///// uncommented once reference counting is added. Currently the objects
  ///// are leaking from the object store.
  int result5 = *(Ray::Get(r5));
  //  int result4 = *(Ray::Get(r4));
  int result6 = *(Ray::Get(r6));
  //  int result3 = *(Ray::Get(r3));
  EXPECT_EQ(result0, 1);
  // EXPECT_EQ(result3, 1);
  // EXPECT_EQ(result4, 2);
  EXPECT_EQ(result5, 3);
  EXPECT_EQ(result6, 12);

  /// create actor and actor function remote call with args passed by value
  ActorHandle<Counter> actor4 = Ray::Actor(Counter::FactoryCreate, 10).Remote();
  auto r7 = actor4.Task(&Counter::Add, 5).Remote();
  auto r8 = actor4.Task(&Counter::Add, 1).Remote();
  auto r9 = actor4.Task(&Counter::Add, 3).Remote();
  auto r10 = actor4.Task(&Counter::Add, 8).Remote();

  int result7 = *(Ray::Get(r7));
  int result8 = *(Ray::Get(r8));
  int result9 = *(Ray::Get(r9));
  //  int result10 = *(Ray::Get(r10));
  EXPECT_EQ(result7, 15);
  EXPECT_EQ(result8, 16);
  EXPECT_EQ(result9, 19);
  //  EXPECT_EQ(result10, 27);

  /// create actor and task function remote call with args passed by reference
  //  ActorHandle<Counter> actor5 = Ray::Actor(Counter::FactoryCreate, r10, 0).Remote();
  ActorHandle<Counter> actor5 = Ray::Actor(Counter::FactoryCreate, 27, 0).Remote();
  //  auto r11 = actor5.Task(&Counter::Add, r0).Remote();
  auto r11 = actor5.Task(&Counter::Add, 1).Remote();
  //  auto r12 = actor5.Task(&Counter::Add, r11).Remote();
  auto r13 = actor5.Task(&Counter::Add, r10).Remote();
  auto r14 = actor5.Task(&Counter::Add, r13).Remote();
  //  auto r15 = Ray::Task(Plus, r0, r11).Remote();
  auto r15 = Ray::Task(Plus, 1, r11).Remote();
  auto r16 = Ray::Task(Plus1, r15).Remote();

  //  int result12 = *(Ray::Get(r12));
  int result14 = *(Ray::Get(r14));
  //  int result11 = *(Ray::Get(r11));
  //  int result13 = *(Ray::Get(r13));
  int result16 = *(Ray::Get(r16));
  //  int result15 = *(Ray::Get(r15));

  //  EXPECT_EQ(result11, 28);
  //  EXPECT_EQ(result12, 56);
  //  EXPECT_EQ(result13, 83);
  //  EXPECT_EQ(result14, 166);
  EXPECT_EQ(result14, 110);
  //  EXPECT_EQ(result15, 29);
  EXPECT_EQ(result16, 30);

  Ray::Shutdown();
}

/// TODO(Guyang Song): Separate default worker from this test.
/// Currently, we compile `default_worker` and `cluster_mode_test` in one single binary,
/// to work around a symbol conflicting issue.
/// This is the main function of the binary, and we use the `is_default_worker` arg to
/// tell if this binary is used as `default_worker` or `cluster_mode_test`.
int main(int argc, char **argv) {
  const char *default_worker_magic = "is_default_worker";
  /// `is_default_worker` is the last arg of `argv`
  if (argc > 1 &&
      memcmp(argv[argc - 1], default_worker_magic, strlen(default_worker_magic)) == 0) {
    default_worker_main(argc, argv);
    return 0;
  }
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
